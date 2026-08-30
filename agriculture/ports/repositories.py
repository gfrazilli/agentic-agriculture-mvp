"""Persistence contracts for agriculture entities and request accounting."""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime
from typing import Any, Literal, Protocol

from agriculture.schemas import AgentSession, Analysis, AnalysisProgress, Feedback, Field

DEFAULT_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
DEFAULT_ANALYSIS_LEASE_SECONDS = 20 * 60


class DailyUsageLimitExceeded(RuntimeError):
    """Raised when a subject has no analysis quota left for a UTC date."""

    def __init__(self, subject: str, usage_date: date, limit: int) -> None:
        self.subject = subject
        self.usage_date = usage_date
        self.limit = limit
        super().__init__(f"Daily usage limit ({limit}) exceeded for {subject!r} on {usage_date}.")


class IdempotencyConflict(RuntimeError):
    """The same idempotency key was reused for a different request payload."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Idempotency key {key!r} is already bound to another request.")


class AnalysisLeaseLost(RuntimeError):
    """The worker no longer owns the analysis attempt it tried to mutate."""


class AnalysisLeaseActive(RuntimeError):
    """A generic write tried to bypass an active analysis worker lease."""


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Previously claimed idempotent request, optionally with its final response."""

    key: str
    request_digest: str
    expires_at: datetime
    response: Mapping[str, Any] | None = None
    pending: bool = True


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """Result of atomically claiming an idempotency key."""

    record: IdempotencyRecord
    is_replay: bool


@dataclass(frozen=True, slots=True)
class RequestClaim:
    """Result of atomically claiming an idempotency key and daily quota."""

    record: IdempotencyRecord
    is_replay: bool
    usage_count: int


@dataclass(frozen=True, slots=True)
class AnalysisLeaseHandle:
    """Unforgeable worker capability plus monotonic fencing metadata.

    The plaintext token exists only in the worker process. Persistence adapters
    store a digest and compare it while holding their atomic write primitive.
    """

    analysis_id: str
    attempt_id: str
    generation: int
    revision: int
    acquired_at: datetime
    expires_at: datetime
    token: str = dataclass_field(repr=False)


@dataclass(frozen=True, slots=True)
class AnalysisWorkClaim:
    """Outcome of atomically claiming one analysis for processing."""

    outcome: Literal["acquired", "busy", "completed", "failed"]
    analysis: Analysis
    lease: AnalysisLeaseHandle | None = None
    recovered: bool = False


class AgricultureRepository(Protocol):
    """Repository boundary shared by local and Google Cloud implementations."""

    def save_field(self, field: Field) -> Field: ...

    def get_field(self, field_id: str) -> Field | None: ...

    def list_fields(self, subject_id: str | None = None) -> list[Field]: ...

    def save_analysis(self, analysis: Analysis) -> Analysis: ...

    def claim_analysis_work(
        self,
        analysis_id: str,
        initial_progress: AnalysisProgress,
        *,
        lease_seconds: int = DEFAULT_ANALYSIS_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> AnalysisWorkClaim: ...

    def checkpoint_analysis_work(
        self,
        analysis: Analysis,
        lease: AnalysisLeaseHandle,
        *,
        lease_seconds: int = DEFAULT_ANALYSIS_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> tuple[Analysis, AnalysisLeaseHandle]: ...

    def finalize_analysis_work(
        self,
        analysis: Analysis,
        lease: AnalysisLeaseHandle,
        *,
        now: datetime | None = None,
    ) -> Analysis: ...

    def get_analysis(self, analysis_id: str) -> Analysis | None: ...

    def list_analyses(self, field_id: str | None = None) -> list[Analysis]: ...

    def save_agent_session(self, session: AgentSession) -> AgentSession: ...

    def get_agent_session(self, session_id: str) -> AgentSession | None: ...

    def list_agent_sessions(self) -> list[AgentSession]: ...

    def save_feedback(self, feedback: Feedback) -> Feedback: ...

    def get_feedback(self, feedback_id: str) -> Feedback | None: ...

    def list_feedback(self, analysis_id: str | None = None) -> list[Feedback]: ...

    def get_idempotency(
        self, key: str, *, now: datetime | None = None
    ) -> IdempotencyRecord | None: ...

    def put_idempotency(
        self,
        key: str,
        response: Mapping[str, Any],
        *,
        request_digest: str,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        now: datetime | None = None,
    ) -> IdempotencyRecord: ...

    def claim_idempotency(
        self,
        key: str,
        request_digest: str,
        *,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        now: datetime | None = None,
    ) -> IdempotencyClaim: ...

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
    ) -> RequestClaim: ...

    def complete_request(
        self,
        key: str,
        response: Mapping[str, Any],
        *,
        request_digest: str,
        now: datetime | None = None,
    ) -> IdempotencyRecord: ...

    def reserve_daily_usage(self, subject: str, usage_date: date, limit: int) -> int: ...

    def get_daily_usage(self, subject: str, usage_date: date) -> int: ...
