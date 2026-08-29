"""In-memory and Google Cloud Tasks queue adapters."""

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from agriculture.adapters.optional import load_google_module
from agriculture.internal.security import TASK_SECRET_HEADER, task_secret_is_valid
from agriculture.ports.tasks import QueuedTask


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_task_name(name: str) -> str:
    normalized = name.strip().strip("/")
    if not normalized or ".." in normalized.split("/"):
        raise ValueError("task name must be a non-empty relative route")
    return normalized


def _task_id(deduplication_key: str | None) -> str:
    if deduplication_key is None:
        return uuid4().hex
    if not deduplication_key.strip():
        raise ValueError("deduplication_key must not be empty")
    return hashlib.sha256(deduplication_key.encode()).hexdigest()[:32]


class InMemoryTaskQueue:
    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = RLock()
        self._tasks: dict[str, QueuedTask] = {}

    def enqueue(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        deduplication_key: str | None = None,
        schedule_at: datetime | None = None,
    ) -> QueuedTask:
        name = _validate_task_name(name)
        task_id = _task_id(deduplication_key)
        now = self._clock()
        if now.tzinfo is None or (schedule_at is not None and schedule_at.tzinfo is None):
            raise ValueError("task timestamps must be timezone-aware")
        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is not None and deduplication_key is not None:
                return self._copy(existing)
            task = QueuedTask(
                id=task_id,
                name=name,
                payload=deepcopy(dict(payload)),
                created_at=now.astimezone(UTC),
                schedule_at=schedule_at.astimezone(UTC) if schedule_at else None,
            )
            self._tasks[task_id] = task
            return self._copy(task)

    @property
    def tasks(self) -> list[QueuedTask]:
        with self._lock:
            return [self._copy(task) for task in self._tasks.values()]

    @staticmethod
    def _copy(task: QueuedTask) -> QueuedTask:
        return QueuedTask(
            id=task.id,
            name=task.name,
            payload=deepcopy(task.payload),
            created_at=task.created_at,
            schedule_at=task.schedule_at,
        )


class CloudTasksQueue:
    """HTTP Cloud Tasks dispatcher with deterministic task IDs for deduplication."""

    def __init__(
        self,
        *,
        project: str,
        location: str,
        queue: str,
        target_base_url: str,
        shared_secret: str = "",
        oidc_service_account_email: str | None = None,
        oidc_audience: str | None = None,
        dispatch_deadline_seconds: int = 900,
        client: Any | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        tasks_v2 = load_google_module("google.cloud.tasks_v2", "google-cloud-tasks")
        api_exceptions = load_google_module("google.api_core.exceptions", "google-api-core")
        timestamp_pb2 = load_google_module("google.protobuf.timestamp_pb2", "protobuf")
        duration_pb2 = load_google_module("google.protobuf.duration_pb2", "protobuf")
        required = {"project": project, "location": location, "queue": queue}
        if missing := [name for name, value in required.items() if not value.strip()]:
            raise ValueError(f"Missing Cloud Tasks configuration: {', '.join(missing)}")
        if not target_base_url.startswith(("https://", "http://")):
            raise ValueError("target_base_url must be an absolute HTTP(S) URL")
        if not task_secret_is_valid(shared_secret):
            raise ValueError(
                "shared_secret must contain at least 32 printable ASCII characters without spaces"
            )
        if not 60 <= dispatch_deadline_seconds <= 1800:
            raise ValueError("dispatch_deadline_seconds must be between 60 and 1800")
        self._client = client or tasks_v2.CloudTasksClient()
        self._parent = self._client.queue_path(project, location, queue)
        self._project = project
        self._location = location
        self._queue = queue
        self._target_base_url = target_base_url.rstrip("/")
        self._shared_secret = shared_secret
        self._oidc_service_account_email = oidc_service_account_email
        self._oidc_audience = oidc_audience
        self._timestamp_type = timestamp_pb2.Timestamp
        self._duration_type = duration_pb2.Duration
        self._dispatch_deadline_seconds = dispatch_deadline_seconds
        self._http_method_post = tasks_v2.HttpMethod.POST
        self._already_exists_error = api_exceptions.AlreadyExists
        self._clock = clock

    def enqueue(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        deduplication_key: str | None = None,
        schedule_at: datetime | None = None,
    ) -> QueuedTask:
        name = _validate_task_name(name)
        task_id = _task_id(deduplication_key)
        created_at = self._clock()
        if created_at.tzinfo is None or (schedule_at is not None and schedule_at.tzinfo is None):
            raise ValueError("task timestamps must be timezone-aware")

        http_request: dict[str, Any] = {
            "http_method": self._http_method_post,
            "url": f"{self._target_base_url}/{name}",
            "headers": {
                "Content-Type": "application/json",
                TASK_SECRET_HEADER: self._shared_secret,
            },
            "body": json.dumps(dict(payload), separators=(",", ":")).encode(),
        }
        if self._oidc_service_account_email:
            oidc_token = {"service_account_email": self._oidc_service_account_email}
            if self._oidc_audience:
                oidc_token["audience"] = self._oidc_audience
            http_request["oidc_token"] = oidc_token

        task: dict[str, Any] = {
            "name": self._client.task_path(self._project, self._location, self._queue, task_id),
            "http_request": http_request,
        }
        dispatch_deadline = self._duration_type()
        dispatch_deadline.FromSeconds(self._dispatch_deadline_seconds)
        task["dispatch_deadline"] = dispatch_deadline
        normalized_schedule = schedule_at.astimezone(UTC) if schedule_at else None
        if normalized_schedule:
            timestamp = self._timestamp_type()
            timestamp.FromDatetime(normalized_schedule)
            task["schedule_time"] = timestamp

        try:
            response = self._client.create_task(parent=self._parent, task=task)
            returned_id = str(response.name).rsplit("/", 1)[-1]
        except self._already_exists_error:
            # Cloud Tasks retains task IDs after completion. A deterministic ID
            # therefore provides the same at-most-once enqueue contract as memory.
            returned_id = task_id
        return QueuedTask(
            id=returned_id,
            name=name,
            payload=deepcopy(dict(payload)),
            created_at=created_at.astimezone(UTC),
            schedule_at=normalized_schedule,
        )
