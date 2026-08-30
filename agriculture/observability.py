"""Small, fail-closed JSON audit logger shared by every service role.

Only explicitly allowlisted scalar identifiers and counters can reach the log
payload.  Free-form text and nested objects are intentionally unsupported so a
future caller cannot accidentally emit prompts, geometry, credentials, or
provider error bodies.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+()\-]{0,199}$")
_ALLOWED_FIELDS = frozenset(
    {
        "agents",
        "analysis_id",
        "channel",
        "component",
        "duration_ms",
        "error_code",
        "error_type",
        "execution_id",
        "field_id",
        "language",
        "model",
        "parent_analysis_id",
        "percent",
        "replayed",
        "requested_zone_count",
        "retryable",
        "scene_count",
        "scene_ids",
        "session_id",
        "stage",
        "status",
        "task_name",
        "tool_name",
        "tools",
        "turn_number",
        "zone_count",
    }
)
_SEQUENCE_FIELDS = frozenset({"agents", "scene_ids", "tools"})
_INTEGER_FIELDS = frozenset(
    {
        "duration_ms",
        "percent",
        "requested_zone_count",
        "scene_count",
        "turn_number",
        "zone_count",
    }
)
_BOOLEAN_FIELDS = frozenset({"replayed", "retryable"})
_DROP = object()
_AUDIT_HANDLER_MARKER = "_agentic_agriculture_audit_handler"


def get_audit_logger(name: str) -> logging.Logger:
    """Return an INFO logger with exactly one local plain-message handler.

    Django does not configure the root logger, and the standalone MCP process
    does not load Django settings at all.  A local non-propagating handler makes
    the audit channel reliable in both runtimes without raising the global log
    level (which could expose verbose HTTP or SDK messages).
    """

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not any(getattr(handler, _AUDIT_HANDLER_MARKER, False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        setattr(handler, _AUDIT_HANDLER_MARKER, True)
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def audit_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one JSON audit event after removing every non-allowlisted value."""

    safe_event = _safe_token(event)
    if safe_event is _DROP:
        raise ValueError("Audit event names must be short machine-readable tokens.")

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "severity": logging.getLevelName(level),
        "event": safe_event,
    }
    for key, value in fields.items():
        if key not in _ALLOWED_FIELDS or value is None:
            continue
        safe_value = _safe_field(key, value)
        if safe_value is not _DROP:
            payload[key] = safe_value

    logger.log(
        level,
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
    )


def _safe_field(key: str, value: Any) -> Any:
    if key in _BOOLEAN_FIELDS:
        return value if isinstance(value, bool) else _DROP
    if key in _INTEGER_FIELDS:
        return value if isinstance(value, int) and not isinstance(value, bool) else _DROP
    if key in _SEQUENCE_FIELDS:
        if not isinstance(value, (list, tuple)):
            return _DROP
        cleaned = [_safe_token(item) for item in value]
        return [item for item in cleaned if item is not _DROP][:100]
    return _safe_token(value)


def _safe_token(value: Any) -> str | object:
    if not isinstance(value, str) or _SAFE_TOKEN.fullmatch(value) is None:
        return _DROP
    return value
