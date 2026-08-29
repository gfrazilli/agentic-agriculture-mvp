import hashlib
import re
import time as system_time
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from django.http import HttpRequest

from agriculture.api.errors import APIError
from agriculture.ports.repositories import (
    AgricultureRepository,
    DailyUsageLimitExceeded,
    IdempotencyConflict,
    IdempotencyRecord,
)

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


@dataclass(frozen=True, slots=True)
class IdempotencyContext:
    scoped_key: str
    request_digest: str


@dataclass(frozen=True, slots=True)
class ServiceResult:
    data: Any
    status: int
    replayed: bool = False

    def as_record(self) -> dict[str, Any]:
        return {"status": self.status, "data": self.data}


def context_from_request(request: HttpRequest, actor_id: str) -> IdempotencyContext:
    raw_key = request.headers.get("Idempotency-Key", "").strip()
    if not raw_key:
        raise APIError(
            code="idempotency_key_required",
            message="Idempotency-Key is required for this operation.",
            status=400,
        )
    if not _KEY_PATTERN.fullmatch(raw_key):
        raise APIError(
            code="invalid_idempotency_key",
            message=(
                "Idempotency-Key must contain 8-128 ASCII letters, numbers, dots, "
                "underscores, colons or hyphens."
            ),
            status=400,
        )

    scoped_material = f"{actor_id}\0{request.path}\0{raw_key}".encode()
    return IdempotencyContext(
        scoped_key=hashlib.sha256(scoped_material).hexdigest(),
        request_digest=hashlib.sha256(request.body).hexdigest(),
    )


def replay_if_present(
    repository: AgricultureRepository,
    context: IdempotencyContext,
) -> ServiceResult | None:
    """Return an existing result before mutable domain state is revalidated."""

    record = repository.get_idempotency(context.scoped_key)
    if record is None:
        return None
    _validate_digest(record, context)
    return _await_result(repository, context, record)


def claim_idempotent_request(
    repository: AgricultureRepository,
    context: IdempotencyContext,
    *,
    now: datetime,
) -> ServiceResult | None:
    try:
        claim = repository.claim_idempotency(
            context.scoped_key,
            context.request_digest,
            now=now,
        )
    except IdempotencyConflict:
        raise _conflict() from None
    if not claim.is_replay:
        return None
    _validate_digest(claim.record, context)
    return _await_result(repository, context, claim.record)


def complete_idempotent_request(
    repository: AgricultureRepository,
    context: IdempotencyContext,
    result: ServiceResult,
    *,
    now: datetime,
) -> None:
    try:
        repository.complete_request(
            context.scoped_key,
            result.as_record(),
            request_digest=context.request_digest,
            now=now,
        )
    except IdempotencyConflict:
        raise _conflict() from None


def claim_limited_request(
    repository: AgricultureRepository,
    context: IdempotencyContext,
    *,
    subject: str,
    daily_limit: int,
    now: datetime,
) -> ServiceResult | None:
    try:
        claim = repository.claim_request(
            context.scoped_key,
            context.request_digest,
            subject,
            now.astimezone(UTC).date(),
            daily_limit,
            now=now,
        )
    except IdempotencyConflict:
        raise _conflict() from None
    except DailyUsageLimitExceeded:
        tomorrow = datetime.combine(
            now.astimezone(UTC).date() + timedelta(days=1),
            time.min,
            tzinfo=UTC,
        )
        retry_after = max(1, int((tomorrow - now.astimezone(UTC)).total_seconds()))
        raise APIError(
            code="daily_analysis_limit_exceeded",
            message="The daily analysis and regrouping limit has been reached.",
            status=429,
            headers={"Retry-After": str(retry_after)},
        ) from None

    if not claim.is_replay:
        return None
    _validate_digest(claim.record, context)
    return _await_result(repository, context, claim.record)


def _validate_digest(record: IdempotencyRecord, context: IdempotencyContext) -> None:
    if record.request_digest != context.request_digest:
        raise _conflict()


def _result_from_record(record: IdempotencyRecord) -> ServiceResult:
    if record.pending or record.response is None:
        raise APIError(
            code="request_in_progress",
            message="A request with this Idempotency-Key is still being processed.",
            status=409,
            headers={"Retry-After": "2"},
        )
    try:
        status = int(record.response["status"])
        data = record.response["data"]
    except (KeyError, TypeError, ValueError):
        raise APIError(
            code="invalid_idempotency_record",
            message="The stored idempotency result is invalid.",
            status=500,
        ) from None
    return ServiceResult(data=data, status=status, replayed=True)


def _await_result(
    repository: AgricultureRepository,
    context: IdempotencyContext,
    record: IdempotencyRecord,
    *,
    timeout_seconds: float = 5.0,
) -> ServiceResult:
    """Wait briefly so concurrent identical requests receive the completed replay."""

    deadline = system_time.monotonic() + timeout_seconds
    current = record
    while current.pending or current.response is None:
        remaining = deadline - system_time.monotonic()
        if remaining <= 0:
            return _result_from_record(current)
        system_time.sleep(min(0.02, remaining))
        refreshed = repository.get_idempotency(context.scoped_key)
        if refreshed is None:
            return _result_from_record(current)
        _validate_digest(refreshed, context)
        current = refreshed
    return _result_from_record(current)


def _conflict() -> APIError:
    return APIError(
        code="idempotency_key_conflict",
        message="This Idempotency-Key was already used with a different request body.",
        status=409,
    )
