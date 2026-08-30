"""Thread-safe, process-local agriculture repository."""

import hashlib
import hmac
import secrets
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from agriculture.domain import AnalysisStateMachine, AnalysisStatus
from agriculture.ports.repositories import (
    DEFAULT_ANALYSIS_LEASE_SECONDS,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    AnalysisLeaseActive,
    AnalysisLeaseHandle,
    AnalysisLeaseLost,
    AnalysisWorkClaim,
    DailyUsageLimitExceeded,
    IdempotencyClaim,
    IdempotencyConflict,
    IdempotencyRecord,
    RequestClaim,
)
from agriculture.schemas import AgentSession, Analysis, AnalysisProgress, Feedback, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _entity_id(entity: BaseModel) -> str:
    value = getattr(entity, "id", None)
    if value is None:
        raise ValueError(f"{type(entity).__name__} must expose a non-null id")
    return str(value)


def _clone[ModelT: BaseModel](model: ModelT) -> ModelT:
    return model.model_copy(deep=True)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _AnalysisLease:
    analysis_id: str
    attempt_id: str
    token_digest: str
    generation: int
    revision: int
    acquired_at: datetime
    expires_at: datetime
    released_at: datetime | None = None


class InMemoryAgricultureRepository:
    """Development adapter with the same atomic request semantics as Firestore."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = RLock()
        self._fields: dict[str, Field] = {}
        self._analyses: dict[str, Analysis] = {}
        self._analysis_leases: dict[str, _AnalysisLease] = {}
        self._agent_sessions: dict[str, AgentSession] = {}
        self._feedback: dict[str, Feedback] = {}
        self._idempotency: dict[str, tuple[IdempotencyRecord, int]] = {}
        self._daily_usage: dict[tuple[str, date], int] = {}

    def _now(self, supplied: datetime | None = None) -> datetime:
        return _as_utc(supplied if supplied is not None else self._clock())

    @staticmethod
    def _validate_key(key: str) -> str:
        normalized = key.strip()
        if not normalized:
            raise ValueError("idempotency key must not be empty")
        return normalized

    @staticmethod
    def _validate_ttl(ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")

    @staticmethod
    def _validate_digest(request_digest: str) -> str:
        normalized = request_digest.strip()
        if not normalized:
            raise ValueError("request_digest must not be empty")
        return normalized

    def _save[ModelT: BaseModel](self, target: dict[str, ModelT], entity: ModelT) -> ModelT:
        with self._lock:
            stored = _clone(entity)
            target[_entity_id(stored)] = stored
            return _clone(stored)

    def _get[ModelT: BaseModel](self, target: dict[str, ModelT], entity_id: str) -> ModelT | None:
        with self._lock:
            entity = target.get(str(entity_id))
            return _clone(entity) if entity is not None else None

    def _list[ModelT: BaseModel](self, target: dict[str, ModelT]) -> list[ModelT]:
        with self._lock:
            return [_clone(target[key]) for key in sorted(target)]

    def save_field(self, field: Field) -> Field:
        return self._save(self._fields, field)

    def get_field(self, field_id: str) -> Field | None:
        return self._get(self._fields, field_id)

    def list_fields(self, subject_id: str | None = None) -> list[Field]:  # noqa: ARG002
        # Field v1 does not expose an ownership attribute yet.
        return self._list(self._fields)

    def save_analysis(self, analysis: Analysis) -> Analysis:
        analysis_id = _entity_id(analysis)
        with self._lock:
            lease = self._analysis_leases.get(analysis_id)
            if lease is not None and lease.released_at is None:
                raise AnalysisLeaseActive(
                    f"Analysis {analysis_id!r} has an unreleased worker lease."
                )
            stored = _clone(analysis)
            self._analyses[analysis_id] = stored
            return _clone(stored)

    def claim_analysis_work(
        self,
        analysis_id: str,
        initial_progress: AnalysisProgress,
        *,
        lease_seconds: int = DEFAULT_ANALYSIS_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> AnalysisWorkClaim:
        """Atomically transition an eligible analysis to a fenced running attempt."""

        self._validate_ttl(lease_seconds)
        analysis_id = str(analysis_id)
        instant = self._now(now)
        token = secrets.token_urlsafe(32)
        attempt_id = str(uuid4())
        with self._lock:
            previous = self._analyses.get(analysis_id)
            if previous is None:
                raise KeyError(f"Analysis {analysis_id!r} does not exist.")
            if previous.status is AnalysisStatus.COMPLETED:
                return AnalysisWorkClaim("completed", _clone(previous))
            if previous.status is AnalysisStatus.FAILED and not (
                previous.error and previous.error.retryable
            ):
                return AnalysisWorkClaim("failed", _clone(previous))

            previous_lease = self._analysis_leases.get(analysis_id)
            if (
                previous_lease is not None
                and previous_lease.released_at is None
                and previous_lease.expires_at > instant
            ):
                return AnalysisWorkClaim("busy", _clone(previous))
            if (
                previous.status is AnalysisStatus.RUNNING
                and previous_lease is None
                and previous.updated_at + timedelta(seconds=lease_seconds) > instant
            ):
                return AnalysisWorkClaim("busy", _clone(previous))

            generation = previous_lease.generation + 1 if previous_lease is not None else 1
            expires_at = instant + timedelta(seconds=lease_seconds)
            running = self._running_attempt(previous, initial_progress, instant)
            stored_lease = _AnalysisLease(
                analysis_id=analysis_id,
                attempt_id=attempt_id,
                token_digest=_token_digest(token),
                generation=generation,
                revision=0,
                acquired_at=instant,
                expires_at=expires_at,
            )
            self._analyses[analysis_id] = _clone(running)
            self._analysis_leases[analysis_id] = stored_lease
            handle = self._lease_handle(stored_lease, token)
            recovered = previous.status is not AnalysisStatus.QUEUED or previous_lease is not None
            return AnalysisWorkClaim(
                "acquired",
                _clone(running),
                lease=handle,
                recovered=recovered,
            )

    def checkpoint_analysis_work(
        self,
        analysis: Analysis,
        lease: AnalysisLeaseHandle,
        *,
        lease_seconds: int = DEFAULT_ANALYSIS_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> tuple[Analysis, AnalysisLeaseHandle]:
        """Persist and renew a running analysis only for the current lease owner."""

        self._validate_ttl(lease_seconds)
        instant = self._now(now)
        with self._lock:
            previous, stored_lease = self._owned_analysis(analysis, lease, instant)
            if analysis.status is not AnalysisStatus.RUNNING:
                raise ValueError("Analysis checkpoints must remain in running status.")
            self._validate_owned_update(previous, analysis)
            renewed = replace(
                stored_lease,
                revision=stored_lease.revision + 1,
                expires_at=instant + timedelta(seconds=lease_seconds),
            )
            saved = _clone(analysis)
            self._analyses[str(saved.id)] = saved
            self._analysis_leases[str(saved.id)] = renewed
            return _clone(saved), self._lease_handle(renewed, lease.token)

    def finalize_analysis_work(
        self,
        analysis: Analysis,
        lease: AnalysisLeaseHandle,
        *,
        now: datetime | None = None,
    ) -> Analysis:
        """Commit COMPLETED/FAILED and release the lease in one critical section."""

        instant = self._now(now)
        with self._lock:
            previous, stored_lease = self._owned_analysis(analysis, lease, instant)
            if analysis.status not in {AnalysisStatus.COMPLETED, AnalysisStatus.FAILED}:
                raise ValueError("A finalized analysis must be completed or failed.")
            self._validate_owned_update(previous, analysis)
            saved = _clone(analysis)
            self._analyses[str(saved.id)] = saved
            self._analysis_leases[str(saved.id)] = replace(
                stored_lease,
                revision=stored_lease.revision + 1,
                released_at=instant,
            )
            return _clone(saved)

    @staticmethod
    def _running_attempt(
        previous: Analysis,
        initial_progress: AnalysisProgress,
        instant: datetime,
    ) -> Analysis:
        progress = AnalysisProgress(
            percent=initial_progress.percent,
            stage=initial_progress.stage,
            message_pt=initial_progress.message_pt,
            message_en=initial_progress.message_en,
            updated_at=instant,
        )
        return Analysis(
            id=previous.id,
            field_id=previous.field_id,
            parent_analysis_id=previous.parent_analysis_id,
            status=AnalysisStatus.RUNNING,
            requested_zone_count=previous.requested_zone_count,
            progress=progress,
            result=None,
            error=None,
            created_at=previous.created_at,
            updated_at=instant,
        )

    @staticmethod
    def _lease_handle(lease: _AnalysisLease, token: str) -> AnalysisLeaseHandle:
        return AnalysisLeaseHandle(
            analysis_id=lease.analysis_id,
            attempt_id=lease.attempt_id,
            generation=lease.generation,
            revision=lease.revision,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            token=token,
        )

    def _owned_analysis(
        self,
        candidate: Analysis,
        handle: AnalysisLeaseHandle,
        instant: datetime,
    ) -> tuple[Analysis, _AnalysisLease]:
        analysis_id = str(candidate.id)
        stored = self._analyses.get(analysis_id)
        lease = self._analysis_leases.get(analysis_id)
        owned = (
            stored is not None
            and lease is not None
            and handle.analysis_id == analysis_id
            and lease.analysis_id == analysis_id
            and lease.released_at is None
            and lease.expires_at > instant
            and lease.attempt_id == handle.attempt_id
            and lease.generation == handle.generation
            and lease.revision == handle.revision
            and hmac.compare_digest(lease.token_digest, _token_digest(handle.token))
        )
        if not owned:
            raise AnalysisLeaseLost(f"Analysis {analysis_id!r} lease ownership was lost.")
        return stored, lease

    @staticmethod
    def _validate_owned_update(previous: Analysis, candidate: Analysis) -> None:
        immutable = (
            candidate.id == previous.id
            and candidate.field_id == previous.field_id
            and candidate.parent_analysis_id == previous.parent_analysis_id
            and candidate.requested_zone_count == previous.requested_zone_count
            and candidate.created_at == previous.created_at
        )
        if not immutable:
            raise ValueError("An owned analysis update cannot change immutable fields.")
        if candidate.updated_at != candidate.progress.updated_at:
            raise ValueError("Analysis and progress timestamps must match.")
        if candidate.updated_at < previous.updated_at:
            raise ValueError("Analysis updated_at cannot move backwards.")
        AnalysisStateMachine.validate_transition(
            previous.status,
            previous.progress.percent,
            candidate.status,
            candidate.progress.percent,
        )

    def get_analysis(self, analysis_id: str) -> Analysis | None:
        return self._get(self._analyses, analysis_id)

    def list_analyses(self, field_id: str | None = None) -> list[Analysis]:
        analyses = self._list(self._analyses)
        if field_id is None:
            return analyses
        return [analysis for analysis in analyses if str(analysis.field_id) == str(field_id)]

    def save_agent_session(self, session: AgentSession) -> AgentSession:
        return self._save(self._agent_sessions, session)

    def get_agent_session(self, session_id: str) -> AgentSession | None:
        return self._get(self._agent_sessions, session_id)

    def list_agent_sessions(self) -> list[AgentSession]:
        return self._list(self._agent_sessions)

    def save_feedback(self, feedback: Feedback) -> Feedback:
        return self._save(self._feedback, feedback)

    def get_feedback(self, feedback_id: str) -> Feedback | None:
        return self._get(self._feedback, feedback_id)

    def list_feedback(self, analysis_id: str | None = None) -> list[Feedback]:
        feedback = self._list(self._feedback)
        if analysis_id is None:
            return feedback
        return [item for item in feedback if str(item.analysis_id) == str(analysis_id)]

    def get_idempotency(self, key: str, *, now: datetime | None = None) -> IdempotencyRecord | None:
        key = self._validate_key(key)
        instant = self._now(now)
        with self._lock:
            stored = self._idempotency.get(key)
            if stored is None:
                return None
            record, _usage_count = stored
            if record.expires_at <= instant:
                del self._idempotency[key]
                return None
            return self._copy_record(record)

    def put_idempotency(
        self,
        key: str,
        response: Mapping[str, Any],
        *,
        request_digest: str,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        key = self._validate_key(key)
        request_digest = self._validate_digest(request_digest)
        self._validate_ttl(ttl_seconds)
        instant = self._now(now)
        with self._lock:
            stored = self._idempotency.get(key)
            if stored is not None and stored[0].expires_at > instant:
                if stored[0].request_digest != request_digest:
                    raise IdempotencyConflict(key)
                return self._copy_record(stored[0])
            record = IdempotencyRecord(
                key=key,
                request_digest=request_digest,
                response=deepcopy(dict(response)),
                pending=False,
                expires_at=instant + timedelta(seconds=ttl_seconds),
            )
            self._idempotency[key] = (record, 0)
        return self._copy_record(record)

    def claim_idempotency(
        self,
        key: str,
        request_digest: str,
        *,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        now: datetime | None = None,
    ) -> IdempotencyClaim:
        """Atomically reserve a key before a service performs any mutation."""

        key = self._validate_key(key)
        request_digest = self._validate_digest(request_digest)
        self._validate_ttl(ttl_seconds)
        instant = self._now(now)

        with self._lock:
            stored = self._idempotency.get(key)
            if stored is not None:
                record, _usage_count = stored
                if record.expires_at > instant:
                    if record.request_digest != request_digest:
                        raise IdempotencyConflict(key)
                    return IdempotencyClaim(self._copy_record(record), True)
                del self._idempotency[key]

            record = IdempotencyRecord(
                key=key,
                request_digest=request_digest,
                response=None,
                pending=True,
                expires_at=instant + timedelta(seconds=ttl_seconds),
            )
            self._idempotency[key] = (record, 0)
            return IdempotencyClaim(self._copy_record(record), False)

    def claim_request(
        self,
        key: str,
        request_digest: str,
        subject: str,
        usage_date: date,
        daily_limit: int,
        *,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        now: datetime | None = None,
    ) -> RequestClaim:
        key = self._validate_key(key)
        request_digest = self._validate_digest(request_digest)
        subject = subject.strip()
        if not subject:
            raise ValueError("subject must not be empty")
        self._validate_ttl(ttl_seconds)
        if daily_limit <= 0:
            raise ValueError("daily_limit must be greater than zero")
        instant = self._now(now)

        with self._lock:
            stored = self._idempotency.get(key)
            if stored is not None:
                record, usage_count = stored
                if record.expires_at > instant:
                    if record.request_digest != request_digest:
                        raise IdempotencyConflict(key)
                    return RequestClaim(self._copy_record(record), True, usage_count)
                del self._idempotency[key]

            usage_key = (subject, usage_date)
            usage_count = self._daily_usage.get(usage_key, 0)
            if usage_count >= daily_limit:
                raise DailyUsageLimitExceeded(subject, usage_date, daily_limit)
            usage_count += 1
            self._daily_usage[usage_key] = usage_count

            record = IdempotencyRecord(
                key=key,
                request_digest=request_digest,
                response=None,
                pending=True,
                expires_at=instant + timedelta(seconds=ttl_seconds),
            )
            self._idempotency[key] = (record, usage_count)
            return RequestClaim(self._copy_record(record), False, usage_count)

    def complete_request(
        self,
        key: str,
        response: Mapping[str, Any],
        *,
        request_digest: str,
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        key = self._validate_key(key)
        request_digest = self._validate_digest(request_digest)
        instant = self._now(now)
        with self._lock:
            stored = self._idempotency.get(key)
            if stored is None or stored[0].expires_at <= instant:
                self._idempotency.pop(key, None)
                raise KeyError(f"No active idempotency claim for {key!r}.")
            previous, usage_count = stored
            if previous.request_digest != request_digest:
                raise IdempotencyConflict(key)
            completed = IdempotencyRecord(
                key=key,
                request_digest=request_digest,
                response=deepcopy(dict(response)),
                pending=False,
                expires_at=previous.expires_at,
            )
            self._idempotency[key] = (completed, usage_count)
            return self._copy_record(completed)

    def reserve_daily_usage(self, subject: str, usage_date: date, limit: int) -> int:
        subject = subject.strip()
        if not subject:
            raise ValueError("subject must not be empty")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        key = (subject, usage_date)
        with self._lock:
            current = self._daily_usage.get(key, 0)
            if current >= limit:
                raise DailyUsageLimitExceeded(subject, usage_date, limit)
            current += 1
            self._daily_usage[key] = current
            return current

    def get_daily_usage(self, subject: str, usage_date: date) -> int:
        with self._lock:
            return self._daily_usage.get((subject, usage_date), 0)

    @staticmethod
    def _copy_record(record: IdempotencyRecord) -> IdempotencyRecord:
        return IdempotencyRecord(
            key=record.key,
            request_digest=record.request_digest,
            expires_at=record.expires_at,
            response=deepcopy(record.response),
            pending=record.pending,
        )
