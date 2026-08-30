"""Read-only preflight checks for the recorded and live demonstration."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from django.conf import settings
from django.core.checks import ERROR, WARNING, run_checks

from agentic_agriculture.auth import fetch_google_id_token
from agentic_agriculture.config import AgenticConfig
from agriculture.checks import backend_configuration
from agriculture.container import (
    get_agent_api_client,
    get_agriculture_service,
    get_analysis_pipeline,
    get_artifact_store,
    get_boundary_provider,
    get_repository,
    get_task_queue,
    reset_container,
)

CheckStatus = Literal["pass", "warning", "fail"]
Requester = Callable[..., Any]
TokenFetcher = Callable[[str], str]

_ENDPOINT_ENV = {
    "web": ("PREFLIGHT_WEB_URL", "WEB_URL"),
    "agent": ("PREFLIGHT_AGENT_URL", "AGENT_API_URL", "AGENT_URL"),
    "mcp": ("PREFLIGHT_MCP_URL", "AGENT_MCP_URL", "MCP_URL"),
}
_AUDIENCE_ENV = {
    "agent": ("PREFLIGHT_AGENT_AUDIENCE", "AGENT_API_AUDIENCE"),
    "mcp": ("PREFLIGHT_MCP_AUDIENCE", "AGENT_MCP_AUDIENCE", "MCP_AUDIENCE"),
}
_SECRET_MARKERS = ("SECRET", "PASSWORD", "TOKEN", "CREDENTIAL", "API_KEY", "PRIVATE_KEY")
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)[^/@\s]+@")


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """One actionable preflight result."""

    id: str
    status: CheckStatus
    message: str
    required: bool

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "id": self.id,
            "status": self.status,
            "required": self.required,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Stable machine- and human-readable result of all checks."""

    environment: str
    checks: tuple[PreflightCheck, ...]

    @property
    def ok(self) -> bool:
        return not any(check.status == "fail" and check.required for check in self.checks)

    @property
    def summary(self) -> dict[str, int]:
        counts = Counter(check.status for check in self.checks)
        return {
            "passed": counts["pass"],
            "warnings": counts["warning"],
            "failures": counts["fail"],
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "environment": self.environment,
            "summary": self.summary,
            "checks": [check.as_dict() for check in self.checks],
        }


class _CheckCollector:
    def __init__(self) -> None:
        self.checks: list[PreflightCheck] = []

    def passed(self, check_id: str, message: str, *, required: bool = True) -> None:
        self.checks.append(PreflightCheck(check_id, "pass", message, required))

    def warning(self, check_id: str, message: str) -> None:
        self.checks.append(PreflightCheck(check_id, "warning", message, False))

    def failed(self, check_id: str, message: str) -> None:
        self.checks.append(PreflightCheck(check_id, "fail", message, True))


def _configured_secret_values() -> tuple[str, ...]:
    values: set[str] = set()
    for name in dir(settings):
        if not any(marker in name.upper() for marker in _SECRET_MARKERS):
            continue
        value = getattr(settings, name, None)
        if isinstance(value, str) and len(value) >= 4:
            values.add(value)
    for name, value in os.environ.items():
        if any(marker in name.upper() for marker in _SECRET_MARKERS) and len(value) >= 4:
            values.add(value)
    return tuple(sorted(values, key=len, reverse=True))


def _safe_error(exc: BaseException) -> str:
    """Describe a failure without echoing credentials from configuration or HTTP headers."""

    message = str(exc).strip()
    for secret in _configured_secret_values():
        message = message.replace(secret, "[redacted]")
    message = _BEARER_PATTERN.sub(r"\1[redacted]", message)
    message = _URL_CREDENTIAL_PATTERN.sub(r"\1[redacted]@", message)
    message = " ".join(message.split())
    if len(message) > 300:
        message = f"{message[:297]}..."
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _selected_value(explicit: str | None, env_names: tuple[str, ...]) -> str | None:
    if explicit is not None:
        return explicit.strip() or None
    return _first_env(env_names)


def _validated_endpoint(raw_url: str, *, role: str) -> str:
    try:
        parsed = urlsplit(raw_url.strip())
        _ = parsed.port
    except ValueError:
        raise ValueError(f"The {role} endpoint is not a valid URL.") from None

    allowed_paths = {"", "/"} if role != "mcp" else {"", "/", "/mcp", "/mcp/"}
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in allowed_paths
    ):
        raise ValueError(
            f"The {role} endpoint must be an HTTP(S) service URL without credentials, "
            "query parameters or fragments."
        )
    if settings.IS_PRODUCTION and parsed.scheme != "https":
        raise ValueError(f"The {role} endpoint must use HTTPS in production.")

    path = "/mcp" if role == "mcp" else ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validated_audience(raw_url: str, *, role: str) -> str:
    try:
        parsed = urlsplit(raw_url.strip())
        _ = parsed.port
    except ValueError:
        raise ValueError(f"The {role} audience is not a valid URL.") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(f"The {role} audience must be an HTTP(S) service origin.")
    if settings.IS_PRODUCTION and parsed.scheme != "https":
        raise ValueError(f"The {role} audience must use HTTPS in production.")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _service_origin(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _authorization_headers(
    url: str,
    *,
    role: str,
    explicit_audience: str | None,
    token_fetcher: TokenFetcher,
) -> dict[str, str]:
    audience = explicit_audience
    if audience is None and (urlsplit(url).hostname or "").lower().endswith(".run.app"):
        audience = _service_origin(url)
    if audience is None:
        return {}

    audience = _validated_audience(audience, role=role)
    token = token_fetcher(audience).strip()
    if not token or "\r" in token or "\n" in token:
        raise ValueError(f"The {role} identity-token provider returned an invalid token.")
    return {"Authorization": f"Bearer {token}"}


def _request_json(
    requester: Requester,
    method: str,
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> Any:
    request_headers = {"Accept": "application/json"}
    request_headers.update(headers or {})
    kwargs: dict[str, Any] = {"headers": request_headers, "timeout": timeout}
    if payload is not None:
        kwargs["json"] = dict(payload)
    response = requester(method, url, **kwargs)
    status_code = int(response.status_code)
    if not 200 <= status_code < 300:
        raise RuntimeError(f"HTTP {status_code} returned by the endpoint.")
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError("The endpoint did not return valid JSON.") from exc


def _check_environment(collector: _CheckCollector) -> None:
    environment = str(settings.APP_ENV).strip().lower()
    if environment not in {"development", "test", "production"}:
        collector.failed(
            "runtime.environment",
            "APP_ENV must be development, test or production; an unknown mode can bypass "
            "production safeguards.",
        )
        return
    if bool(settings.IS_PRODUCTION) != (environment == "production"):
        collector.failed(
            "runtime.environment",
            "APP_ENV and the derived production flag disagree.",
        )
        return
    if not settings.IS_PRODUCTION:
        collector.passed(
            "runtime.environment",
            f"Recognized {environment} configuration; local backends are allowed.",
        )
        return

    insecure = []
    if settings.DEBUG:
        insecure.append("DEBUG")
    if not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
        insecure.append("secure cookies")
    if not settings.SECURE_SSL_REDIRECT:
        insecure.append("HTTPS redirect")
    if settings.SECURE_HSTS_SECONDS <= 0:
        insecure.append("HSTS")
    if insecure:
        collector.failed(
            "runtime.environment",
            f"Production security settings are incomplete: {', '.join(insecure)}.",
        )
    else:
        collector.passed(
            "runtime.environment",
            "Production mode has DEBUG disabled, secure cookies, HTTPS redirect and HSTS.",
        )


def _check_django(collector: _CheckCollector) -> None:
    try:
        messages = run_checks(include_deployment_checks=settings.IS_PRODUCTION)
    except Exception as exc:
        collector.failed("django.system_checks", _safe_error(exc))
        return

    errors = [message for message in messages if message.level >= ERROR]
    warnings = [message for message in messages if WARNING <= message.level < ERROR]
    if errors:
        labels = ", ".join(message.id or "unidentified" for message in errors)
        collector.failed("django.system_checks", f"Django security checks failed: {labels}.")
    elif warnings:
        labels = ", ".join(message.id or "unidentified" for message in warnings)
        collector.warning("django.system_checks", f"Django security warnings: {labels}.")
    else:
        collector.passed("django.system_checks", "Django security checks passed.")


def _check_backends(collector: _CheckCollector) -> None:
    labels = {
        "backend_names": "Backend names are supported.",
        "geospatial_processing": "Geospatial processing limits and modes are valid.",
        "firestore": "The selected persistence repository is configured.",
        "cloud_storage": "The selected artifact store is configured.",
        "cloud_tasks": "The selected task queue is configured.",
        "production_backends": "The backend set is valid for the current environment.",
    }
    try:
        configured = backend_configuration()
    except Exception as exc:
        collector.failed("backends.configuration", _safe_error(exc))
        return
    for key, message in labels.items():
        check_id = f"backends.{key}"
        if configured.get(key) is True:
            collector.passed(check_id, message)
        else:
            collector.failed(check_id, f"{message.removesuffix('.')} is not ready.")


def _check_agent_configuration(collector: _CheckCollector) -> AgenticConfig | None:
    try:
        config = AgenticConfig.from_env()
    except Exception as exc:
        collector.failed("agent.configuration", _safe_error(exc))
        return None
    mcp_mode = "enabled" if config.mcp_enabled else "disabled"
    collector.passed(
        "agent.configuration",
        f"ADK configuration uses {config.model}; MCP tools are {mcp_mode}.",
    )
    return config


def _check_runtime_components(collector: _CheckCollector) -> None:
    factories: tuple[tuple[str, str, Callable[[], Any]], ...] = (
        ("runtime.repository", "repository", get_repository),
        ("runtime.artifacts", "artifact store", get_artifact_store),
        ("runtime.tasks", "task queue", get_task_queue),
        ("runtime.service", "agriculture service", get_agriculture_service),
    )
    reset_container()
    try:
        for check_id, label, factory in factories:
            try:
                instance = factory()
            except Exception as exc:
                collector.failed(check_id, f"Could not construct the {label}: {_safe_error(exc)}")
            else:
                collector.passed(
                    check_id,
                    f"Configured {label}: {type(instance).__name__}.",
                )

        if not str(settings.AGENT_API_URL).strip():
            message = "AGENT_API_URL is not configured; Django cannot send Gemini turns."
            if settings.IS_PRODUCTION:
                collector.failed("runtime.agent_gateway", message)
            else:
                collector.warning("runtime.agent_gateway", message)
        else:
            try:
                gateway = get_agent_api_client()
            except Exception as exc:
                collector.failed(
                    "runtime.agent_gateway",
                    f"Could not construct the private agent gateway: {_safe_error(exc)}",
                )
            else:
                collector.passed(
                    "runtime.agent_gateway",
                    f"Configured private agent gateway: {type(gateway).__name__}.",
                )

        try:
            boundary = get_boundary_provider()
        except Exception as exc:
            collector.failed(
                "runtime.boundary",
                f"Could not construct the boundary provider: {_safe_error(exc)}",
            )
        else:
            if boundary is None and settings.BOUNDARY_BACKEND == "fixture":
                collector.passed(
                    "runtime.boundary",
                    "Fixture boundary mode is configured; no live provider is required locally.",
                )
            elif boundary is not None:
                collector.passed(
                    "runtime.boundary",
                    f"Configured boundary provider: {type(boundary).__name__}.",
                )
            else:
                collector.failed(
                    "runtime.boundary", "The selected boundary backend created no provider."
                )

        try:
            pipeline = get_analysis_pipeline()
        except Exception as exc:
            collector.failed(
                "runtime.analysis_pipeline",
                f"Could not construct the analysis pipeline: {_safe_error(exc)}",
            )
        else:
            if pipeline is None and settings.ANALYSIS_PIPELINE_BACKEND == "disabled":
                collector.passed(
                    "runtime.analysis_pipeline",
                    "Analysis pipeline is intentionally disabled for local fixture mode.",
                )
            elif pipeline is not None:
                collector.passed(
                    "runtime.analysis_pipeline",
                    f"Configured analysis pipeline: {type(pipeline).__name__}.",
                )
            else:
                collector.failed(
                    "runtime.analysis_pipeline",
                    "The selected analysis backend created no pipeline.",
                )
    finally:
        reset_container()


def _check_web_endpoint(
    collector: _CheckCollector,
    *,
    raw_url: str | None,
    requester: Requester,
    timeout: float,
) -> None:
    if raw_url is None:
        collector.warning(
            "endpoint.web",
            "Web URL was not provided; live and readiness probes were skipped.",
        )
        return
    try:
        base_url = _validated_endpoint(raw_url, role="web")
        live = _request_json(requester, "GET", f"{base_url}/live", timeout=timeout)
        ready = _request_json(requester, "GET", f"{base_url}/ready", timeout=timeout)
        if not isinstance(live, Mapping) or live.get("status") != "ok":
            raise RuntimeError("The liveness payload did not report status=ok.")
        if not isinstance(ready, Mapping) or ready.get("status") != "ready":
            raise RuntimeError("The readiness payload did not report status=ready.")
        readiness_checks = ready.get("checks")
        if not isinstance(readiness_checks, Mapping) or not all(readiness_checks.values()):
            raise RuntimeError("One or more readiness checks did not pass.")
    except Exception as exc:
        collector.failed("endpoint.web", f"Web probe failed: {_safe_error(exc)}")
    else:
        collector.passed("endpoint.web", f"Web liveness and readiness passed at {base_url}.")


def _check_agent_endpoint(
    collector: _CheckCollector,
    *,
    raw_url: str | None,
    audience: str | None,
    app_name: str,
    requester: Requester,
    token_fetcher: TokenFetcher,
    timeout: float,
) -> None:
    if raw_url is None:
        collector.warning("endpoint.agent", "Agent URL was not provided; ADK probe was skipped.")
        return
    try:
        base_url = _validated_endpoint(raw_url, role="agent")
        headers = _authorization_headers(
            base_url,
            role="agent",
            explicit_audience=audience,
            token_fetcher=token_fetcher,
        )
        payload = _request_json(
            requester,
            "GET",
            f"{base_url}/list-apps",
            timeout=timeout,
            headers=headers,
        )
        if not isinstance(payload, list) or app_name not in payload:
            raise RuntimeError(f"ADK application {app_name!r} was not listed.")
    except Exception as exc:
        collector.failed("endpoint.agent", f"Agent probe failed: {_safe_error(exc)}")
    else:
        collector.passed(
            "endpoint.agent",
            f"Private ADK endpoint listed {app_name} at {base_url}.",
        )


def _check_mcp_endpoint(
    collector: _CheckCollector,
    *,
    raw_url: str | None,
    audience: str | None,
    requester: Requester,
    token_fetcher: TokenFetcher,
    timeout: float,
) -> None:
    if raw_url is None:
        collector.warning("endpoint.mcp", "MCP URL was not provided; MCP probe was skipped.")
        return
    try:
        mcp_url = _validated_endpoint(raw_url, role="mcp")
        headers = _authorization_headers(
            mcp_url,
            role="mcp",
            explicit_audience=audience,
            token_fetcher=token_fetcher,
        )
        headers["Accept"] = "application/json, text/event-stream"
        payload = _request_json(
            requester,
            "POST",
            mcp_url,
            timeout=timeout,
            headers=headers,
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "agentic-agriculture-preflight",
                        "version": "1.0",
                    },
                },
            },
        )
        result = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(result, Mapping) or not result.get("serverInfo"):
            raise RuntimeError("The MCP initialize response did not include serverInfo.")
    except Exception as exc:
        collector.failed("endpoint.mcp", f"MCP probe failed: {_safe_error(exc)}")
    else:
        collector.passed("endpoint.mcp", f"MCP initialize handshake passed at {mcp_url}.")


def run_demo_preflight(
    *,
    web_url: str | None = None,
    agent_url: str | None = None,
    mcp_url: str | None = None,
    agent_audience: str | None = None,
    mcp_audience: str | None = None,
    timeout: float = 10.0,
    requester: Requester = httpx.request,
    token_fetcher: TokenFetcher = fetch_google_id_token,
) -> PreflightReport:
    """Run deterministic configuration checks plus optional live HTTP probes."""

    collector = _CheckCollector()
    if not 0 < timeout <= 60:
        collector.failed(
            "preflight.timeout", "Timeout must be greater than 0 and at most 60 seconds."
        )
        timeout = 10.0

    _check_environment(collector)
    _check_django(collector)
    _check_backends(collector)
    agent_config = _check_agent_configuration(collector)
    _check_runtime_components(collector)

    selected_web_url = _selected_value(web_url, _ENDPOINT_ENV["web"])
    selected_agent_url = _selected_value(agent_url, _ENDPOINT_ENV["agent"])
    selected_mcp_url = _selected_value(mcp_url, _ENDPOINT_ENV["mcp"])
    selected_agent_audience = _selected_value(agent_audience, _AUDIENCE_ENV["agent"])
    selected_mcp_audience = _selected_value(mcp_audience, _AUDIENCE_ENV["mcp"])

    _check_web_endpoint(
        collector,
        raw_url=selected_web_url,
        requester=requester,
        timeout=timeout,
    )
    _check_agent_endpoint(
        collector,
        raw_url=selected_agent_url,
        audience=selected_agent_audience,
        app_name=agent_config.app_name if agent_config is not None else "agentic_agriculture",
        requester=requester,
        token_fetcher=token_fetcher,
        timeout=timeout,
    )
    _check_mcp_endpoint(
        collector,
        raw_url=selected_mcp_url,
        audience=selected_mcp_audience,
        requester=requester,
        token_fetcher=token_fetcher,
        timeout=timeout,
    )

    return PreflightReport(
        environment=str(settings.APP_ENV).strip().lower(),
        checks=tuple(collector.checks),
    )
