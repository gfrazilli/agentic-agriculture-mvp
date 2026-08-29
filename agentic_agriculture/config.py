"""Validated, environment-backed configuration for the Gemini agent graph."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlsplit

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_MCP_URL = "http://geospatial-mcp:8090/mcp"
_MODEL_VERSION = re.compile(r"^gemini-(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:[-.]|$)")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _read_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean value.")


def _read_float(value: str, *, name: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise ValueError(f"{name} must be a number.") from None
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return result


def _validate_model(model: str) -> str:
    normalized = model.strip()
    match = _MODEL_VERSION.match(normalized)
    if match is None:
        raise ValueError("AGENT_MODEL must name a Gemini model with an explicit version.")
    version = (int(match.group("major")), int(match.group("minor") or 0))
    if version < (3, 5):
        raise ValueError("AGENT_MODEL must be Gemini 3.5 or newer.")
    return normalized


def _validate_url(url: str, *, name: str, allow_internal_http: bool) -> str:
    normalized = url.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL.")
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError(f"{name} cannot contain credentials, a query or a fragment.")
    try:
        _ = parsed.port
    except ValueError:
        raise ValueError(f"{name} contains an invalid port.") from None
    if parsed.scheme == "http":
        hostname = parsed.hostname.lower()
        internal = (
            hostname in {"localhost", "127.0.0.1", "::1"}
            or "." not in hostname
            or hostname.endswith(".local")
        )
        if not allow_internal_http or not internal:
            raise ValueError(f"{name} must use HTTPS outside a local container network.")
    return normalized


@dataclass(frozen=True, slots=True)
class AgenticConfig:
    """Configuration needed to build the ADK application without doing I/O."""

    model: str = DEFAULT_MODEL
    app_name: str = "agentic_agriculture"
    mcp_enabled: bool = True
    mcp_url: str = DEFAULT_MCP_URL
    mcp_audience: str | None = None
    mcp_timeout_seconds: float = 15.0
    mcp_tool_cache_seconds: float = 300.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", _validate_model(self.model))
        app_name = self.app_name.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", app_name):
            raise ValueError(
                "AGENT_APP_NAME must contain lowercase letters, digits or underscores."
            )
        object.__setattr__(self, "app_name", app_name)
        if self.mcp_enabled:
            object.__setattr__(
                self,
                "mcp_url",
                _validate_url(self.mcp_url, name="AGENT_MCP_URL", allow_internal_http=True),
            )
        if self.mcp_audience is not None:
            audience = _validate_url(
                self.mcp_audience,
                name="AGENT_MCP_AUDIENCE",
                allow_internal_http=False,
            )
            parsed_audience = urlsplit(audience)
            if parsed_audience.path not in {"", "/"}:
                raise ValueError("AGENT_MCP_AUDIENCE must be the Cloud Run service origin.")
            object.__setattr__(self, "mcp_audience", audience)
        if not isfinite(self.mcp_timeout_seconds) or self.mcp_timeout_seconds <= 0:
            raise ValueError("AGENT_MCP_TIMEOUT_SECONDS must be greater than zero.")
        if not isfinite(self.mcp_tool_cache_seconds) or self.mcp_tool_cache_seconds <= 0:
            raise ValueError("AGENT_MCP_TOOL_CACHE_SECONDS must be greater than zero.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AgenticConfig:
        """Build configuration from a mapping, defaulting to ``os.environ``."""

        values = os.environ if env is None else env
        audience = values.get("AGENT_MCP_AUDIENCE") or values.get("MCP_AUDIENCE") or None
        return cls(
            model=values.get("AGENT_MODEL", values.get("GEMINI_MODEL", DEFAULT_MODEL)),
            app_name=values.get("AGENT_APP_NAME", "agentic_agriculture"),
            mcp_enabled=_read_bool(
                values.get("AGENT_MCP_ENABLED", "true"),
                name="AGENT_MCP_ENABLED",
            ),
            mcp_url=values.get("AGENT_MCP_URL", values.get("MCP_URL", DEFAULT_MCP_URL)),
            mcp_audience=audience,
            mcp_timeout_seconds=_read_float(
                values.get("AGENT_MCP_TIMEOUT_SECONDS", "15"),
                name="AGENT_MCP_TIMEOUT_SECONDS",
            ),
            mcp_tool_cache_seconds=_read_float(
                values.get("AGENT_MCP_TOOL_CACHE_SECONDS", "300"),
                name="AGENT_MCP_TOOL_CACHE_SECONDS",
            ),
        )
