"""Asynchronous task dispatch port."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class QueuedTask:
    id: str
    name: str
    payload: Mapping[str, Any]
    created_at: datetime
    schedule_at: datetime | None = None


class TaskQueue(Protocol):
    def enqueue(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        deduplication_key: str | None = None,
        schedule_at: datetime | None = None,
    ) -> QueuedTask: ...
