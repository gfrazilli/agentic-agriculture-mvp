"""Thread-safe, process-local agriculture repository."""

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import RLock
from typing import Any

from pydantic import BaseModel

from agriculture.ports.repositories import (
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    DailyUsageLimitExceeded,
    IdempotencyClaim,
    IdempotencyConflict,
    IdempotencyRecord,
    RequestClaim,
)
from agriculture.schemas import AgentSession, Analysis, Feedback, Field


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


class InMemoryAgricultureRepository:
    """Development adapter with the same atomic request semantics as Firestore."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = RLock()
        self._fields: dict[str, Field] = {}
        self._analyses: dict[str, Analysis] = {}
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
        return self._save(self._analyses, analysis)

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
