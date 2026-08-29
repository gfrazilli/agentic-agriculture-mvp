"""Google Cloud Firestore agriculture repository."""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from agriculture.adapters.optional import load_google_module
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
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _normalized_now(value: datetime | None) -> datetime:
    return _as_utc(value if value is not None else _utc_now())


def _validate_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _safe_document_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _deserialize[ModelT: BaseModel](model_type: type[ModelT], payload: Mapping[str, Any]) -> ModelT:
    # Strict Pydantic contracts intentionally reject Python UUID strings. JSON
    # validation is the correct boundary for documents stored using mode="json".
    return model_type.model_validate_json(json.dumps(dict(payload), default=_json_default))


class FirestoreRepository:
    """Firestore-backed repository with transactional idempotency and quota claims."""

    FIELDS = "fields"
    ANALYSES = "analyses"
    AGENT_SESSIONS = "agent_sessions"
    FEEDBACK = "feedback"
    IDEMPOTENCY = "idempotency_keys"
    DAILY_USAGE = "daily_usage"

    def __init__(
        self,
        *,
        project: str | None = None,
        database: str | None = None,
        client: Any | None = None,
    ) -> None:
        firestore = load_google_module("google.cloud.firestore", "google-cloud-firestore")
        if client is None:
            kwargs = {"project": project}
            if database:
                kwargs["database"] = database
            client = firestore.Client(**kwargs)
        self._client = client
        self._firestore = firestore

    @staticmethod
    def _entity_id(entity: BaseModel) -> str:
        value = getattr(entity, "id", None)
        if value is None:
            raise ValueError(f"{type(entity).__name__} must expose a non-null id")
        return str(value)

    def _save[ModelT: BaseModel](self, collection: str, entity: ModelT) -> ModelT:
        self._client.collection(collection).document(self._entity_id(entity)).set(
            entity.model_dump(mode="json")
        )
        return entity.model_copy(deep=True)

    def _get[ModelT: BaseModel](
        self, collection: str, entity_id: str, model_type: type[ModelT]
    ) -> ModelT | None:
        snapshot = self._client.collection(collection).document(str(entity_id)).get()
        if not snapshot.exists:
            return None
        return _deserialize(model_type, snapshot.to_dict())

    def _list[ModelT: BaseModel](self, collection: str, model_type: type[ModelT]) -> list[ModelT]:
        entities = [
            _deserialize(model_type, snapshot.to_dict())
            for snapshot in self._client.collection(collection).stream()
            if snapshot.exists
        ]
        return sorted(entities, key=lambda entity: self._entity_id(entity))

    def save_field(self, field: Field) -> Field:
        return self._save(self.FIELDS, field)

    def get_field(self, field_id: str) -> Field | None:
        return self._get(self.FIELDS, field_id, Field)

    def list_fields(self, subject_id: str | None = None) -> list[Field]:  # noqa: ARG002
        # Field v1 has no ownership field. Authentication/authorization remains
        # outside this repository until the contract can enforce it explicitly.
        return self._list(self.FIELDS, Field)

    def save_analysis(self, analysis: Analysis) -> Analysis:
        return self._save(self.ANALYSES, analysis)

    def get_analysis(self, analysis_id: str) -> Analysis | None:
        return self._get(self.ANALYSES, analysis_id, Analysis)

    def list_analyses(self, field_id: str | None = None) -> list[Analysis]:
        analyses = self._list(self.ANALYSES, Analysis)
        if field_id is None:
            return analyses
        return [analysis for analysis in analyses if str(analysis.field_id) == str(field_id)]

    def save_agent_session(self, session: AgentSession) -> AgentSession:
        return self._save(self.AGENT_SESSIONS, session)

    def get_agent_session(self, session_id: str) -> AgentSession | None:
        return self._get(self.AGENT_SESSIONS, session_id, AgentSession)

    def list_agent_sessions(self) -> list[AgentSession]:
        return self._list(self.AGENT_SESSIONS, AgentSession)

    def save_feedback(self, feedback: Feedback) -> Feedback:
        return self._save(self.FEEDBACK, feedback)

    def get_feedback(self, feedback_id: str) -> Feedback | None:
        return self._get(self.FEEDBACK, feedback_id, Feedback)

    def list_feedback(self, analysis_id: str | None = None) -> list[Feedback]:
        feedback = self._list(self.FEEDBACK, Feedback)
        if analysis_id is None:
            return feedback
        return [item for item in feedback if str(item.analysis_id) == str(analysis_id)]

    def _idempotency_ref(self, key: str):
        return self._client.collection(self.IDEMPOTENCY).document(_safe_document_id(key))

    def _usage_ref(self, subject: str, usage_date: date):
        composite_key = f"{subject}\0{usage_date.isoformat()}"
        return self._client.collection(self.DAILY_USAGE).document(_safe_document_id(composite_key))

    @staticmethod
    def _record(payload: Mapping[str, Any]) -> IdempotencyRecord:
        return IdempotencyRecord(
            key=str(payload["key"]),
            request_digest=str(payload["request_digest"]),
            expires_at=_as_utc(payload["expires_at"]),
            response=deepcopy(payload.get("response")),
            pending=bool(payload.get("pending", True)),
        )

    @staticmethod
    def _record_payload(record: IdempotencyRecord, *, usage_count: int) -> dict[str, Any]:
        return {
            "key": record.key,
            "request_digest": record.request_digest,
            "expires_at": record.expires_at,
            "response": deepcopy(record.response),
            "pending": record.pending,
            "usage_count": usage_count,
        }

    def get_idempotency(self, key: str, *, now: datetime | None = None) -> IdempotencyRecord | None:
        key = _validate_text(key, "idempotency key")
        instant = _normalized_now(now)
        ref = self._idempotency_ref(key)
        snapshot = ref.get()
        if not snapshot.exists:
            return None
        record = self._record(snapshot.to_dict())
        if record.expires_at <= instant:
            return None
        return record

    def put_idempotency(
        self,
        key: str,
        response: Mapping[str, Any],
        *,
        request_digest: str,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        key = _validate_text(key, "idempotency key")
        request_digest = _validate_text(request_digest, "request_digest")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        instant = _normalized_now(now)
        ref = self._idempotency_ref(key)
        transaction = self._client.transaction()

        @self._firestore.transactional
        def store(transaction):
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                existing_payload = snapshot.to_dict()
                existing = self._record(existing_payload)
                if existing.expires_at > instant:
                    if existing.request_digest != request_digest:
                        raise IdempotencyConflict(key)
                    return existing
            record = IdempotencyRecord(
                key=key,
                request_digest=request_digest,
                response=deepcopy(dict(response)),
                pending=False,
                expires_at=instant + timedelta(seconds=ttl_seconds),
            )
            transaction.set(ref, self._record_payload(record, usage_count=0))
            return record

        return store(transaction)

    def claim_idempotency(
        self,
        key: str,
        request_digest: str,
        *,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        now: datetime | None = None,
    ) -> IdempotencyClaim:
        """Atomically reserve a key before a service performs any mutation."""

        key = _validate_text(key, "idempotency key")
        request_digest = _validate_text(request_digest, "request_digest")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        instant = _normalized_now(now)
        ref = self._idempotency_ref(key)
        transaction = self._client.transaction()

        @self._firestore.transactional
        def claim(transaction):
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                existing_payload = snapshot.to_dict()
                existing = self._record(existing_payload)
                if existing.expires_at > instant:
                    if existing.request_digest != request_digest:
                        raise IdempotencyConflict(key)
                    return IdempotencyClaim(existing, True)

            record = IdempotencyRecord(
                key=key,
                request_digest=request_digest,
                response=None,
                pending=True,
                expires_at=instant + timedelta(seconds=ttl_seconds),
            )
            transaction.set(ref, self._record_payload(record, usage_count=0))
            return IdempotencyClaim(record, False)

        return claim(transaction)

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
        key = _validate_text(key, "idempotency key")
        request_digest = _validate_text(request_digest, "request_digest")
        subject = _validate_text(subject, "subject")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if daily_limit <= 0:
            raise ValueError("daily_limit must be greater than zero")
        instant = _normalized_now(now)
        idempotency_ref = self._idempotency_ref(key)
        usage_ref = self._usage_ref(subject, usage_date)
        transaction = self._client.transaction()

        @self._firestore.transactional
        def claim(transaction):
            idempotency_snapshot = idempotency_ref.get(transaction=transaction)
            if idempotency_snapshot.exists:
                payload = idempotency_snapshot.to_dict()
                record = self._record(payload)
                if record.expires_at > instant:
                    if record.request_digest != request_digest:
                        raise IdempotencyConflict(key)
                    return RequestClaim(record, True, int(payload.get("usage_count", 0)))

            usage_snapshot = usage_ref.get(transaction=transaction)
            usage_count = (
                int(usage_snapshot.to_dict().get("count", 0)) if usage_snapshot.exists else 0
            )
            if usage_count >= daily_limit:
                raise DailyUsageLimitExceeded(subject, usage_date, daily_limit)
            usage_count += 1
            record = IdempotencyRecord(
                key=key,
                request_digest=request_digest,
                response=None,
                pending=True,
                expires_at=instant + timedelta(seconds=ttl_seconds),
            )
            transaction.set(
                usage_ref,
                {
                    "subject": subject,
                    "usage_date": usage_date.isoformat(),
                    "count": usage_count,
                    "updated_at": instant,
                },
            )
            transaction.set(
                idempotency_ref,
                self._record_payload(record, usage_count=usage_count),
            )
            return RequestClaim(record, False, usage_count)

        return claim(transaction)

    def complete_request(
        self,
        key: str,
        response: Mapping[str, Any],
        *,
        request_digest: str,
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        key = _validate_text(key, "idempotency key")
        request_digest = _validate_text(request_digest, "request_digest")
        instant = _normalized_now(now)
        ref = self._idempotency_ref(key)
        transaction = self._client.transaction()

        @self._firestore.transactional
        def complete(transaction):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(f"No active idempotency claim for {key!r}.")
            payload = snapshot.to_dict()
            previous = self._record(payload)
            if previous.expires_at <= instant:
                raise KeyError(f"No active idempotency claim for {key!r}.")
            if previous.request_digest != request_digest:
                raise IdempotencyConflict(key)
            completed = IdempotencyRecord(
                key=key,
                request_digest=request_digest,
                response=deepcopy(dict(response)),
                pending=False,
                expires_at=previous.expires_at,
            )
            transaction.set(
                ref,
                self._record_payload(completed, usage_count=int(payload.get("usage_count", 0))),
            )
            return completed

        return complete(transaction)

    def reserve_daily_usage(self, subject: str, usage_date: date, limit: int) -> int:
        subject = _validate_text(subject, "subject")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        ref = self._usage_ref(subject, usage_date)
        transaction = self._client.transaction()
        instant = _utc_now()

        @self._firestore.transactional
        def reserve(transaction):
            snapshot = ref.get(transaction=transaction)
            count = int(snapshot.to_dict().get("count", 0)) if snapshot.exists else 0
            if count >= limit:
                raise DailyUsageLimitExceeded(subject, usage_date, limit)
            count += 1
            transaction.set(
                ref,
                {
                    "subject": subject,
                    "usage_date": usage_date.isoformat(),
                    "count": count,
                    "updated_at": instant,
                },
            )
            return count

        return reserve(transaction)

    def get_daily_usage(self, subject: str, usage_date: date) -> int:
        subject = _validate_text(subject, "subject")
        snapshot = self._usage_ref(subject, usage_date).get()
        return int(snapshot.to_dict().get("count", 0)) if snapshot.exists else 0
