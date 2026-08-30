from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from agriculture.preflight import run_demo_preflight

_PREFLIGHT_ENV = (
    "PREFLIGHT_WEB_URL",
    "WEB_URL",
    "PREFLIGHT_AGENT_URL",
    "AGENT_API_URL",
    "AGENT_URL",
    "PREFLIGHT_MCP_URL",
    "AGENT_MCP_URL",
    "MCP_URL",
    "PREFLIGHT_AGENT_AUDIENCE",
    "AGENT_API_AUDIENCE",
    "PREFLIGHT_MCP_AUDIENCE",
    "AGENT_MCP_AUDIENCE",
    "MCP_AUDIENCE",
)


@pytest.fixture(autouse=True)
def clear_preflight_environment(monkeypatch):
    for name in _PREFLIGHT_ENV:
        monkeypatch.delenv(name, raising=False)


class FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def _checks_by_id(report):
    return {check.id: check for check in report.checks}


def test_local_preflight_passes_with_explicit_skipped_endpoint_warnings():
    report = run_demo_preflight()

    checks = _checks_by_id(report)
    assert report.ok is True
    assert report.environment == "development"
    assert checks["runtime.repository"].status == "pass"
    assert "InMemoryAgricultureRepository" in checks["runtime.repository"].message
    assert checks["runtime.analysis_pipeline"].status == "pass"
    assert checks["runtime.agent_gateway"].status == "warning"
    assert checks["endpoint.web"].status == "warning"
    assert checks["endpoint.agent"].required is False
    assert checks["endpoint.mcp"].status == "warning"


def test_unknown_environment_is_a_required_failure(settings):
    settings.APP_ENV = "preview"
    settings.IS_PRODUCTION = False

    report = run_demo_preflight()

    check = _checks_by_id(report)["runtime.environment"]
    assert report.ok is False
    assert check.status == "fail"
    assert check.required is True


def test_insecure_production_configuration_fails_closed(settings):
    settings.APP_ENV = "production"
    settings.IS_PRODUCTION = True
    settings.DEBUG = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.SECURE_SSL_REDIRECT = False
    settings.SECURE_HSTS_SECONDS = 0

    report = run_demo_preflight()

    check = _checks_by_id(report)["runtime.environment"]
    assert check.status == "fail"
    assert "DEBUG" in check.message
    assert "secure cookies" in check.message


def test_missing_agent_gateway_is_a_required_failure_in_production(settings):
    settings.APP_ENV = "production"
    settings.IS_PRODUCTION = True
    settings.AGENT_API_URL = ""

    report = run_demo_preflight()

    check = _checks_by_id(report)["runtime.agent_gateway"]
    assert check.status == "fail"
    assert check.required is True


def test_configured_local_agent_gateway_is_constructed(settings):
    settings.AGENT_API_URL = "http://agent:8001"
    settings.AGENT_API_AUDIENCE = ""

    report = run_demo_preflight()

    check = _checks_by_id(report)["runtime.agent_gateway"]
    assert check.status == "pass"
    assert "AgentAPIClient" in check.message


def test_invalid_gemini_model_is_reported(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "gemini-2.5-flash")

    report = run_demo_preflight()

    check = _checks_by_id(report)["agent.configuration"]
    assert report.ok is False
    assert check.status == "fail"
    assert "Gemini 3.5 or newer" in check.message


def test_runtime_factory_errors_are_redacted(monkeypatch, settings):
    secret = "do-not-print-this-secret-value"
    settings.CLOUD_TASKS_SHARED_SECRET = secret

    def broken_repository():
        raise RuntimeError(f"credential rejected: {secret}")

    monkeypatch.setattr("agriculture.preflight.get_repository", broken_repository)

    report = run_demo_preflight()

    message = _checks_by_id(report)["runtime.repository"].message
    assert report.ok is False
    assert secret not in message
    assert "[redacted]" in message


def test_all_provided_endpoints_are_probed_with_expected_protocols():
    calls = []

    def requester(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/live"):
            return FakeResponse({"status": "ok"})
        if url.endswith("/ready"):
            return FakeResponse({"status": "ready", "checks": {"backends": True}})
        if url.endswith("/list-apps"):
            return FakeResponse(["agentic_agriculture"])
        if url.endswith("/mcp"):
            return FakeResponse({"result": {"serverInfo": {"name": "geospatial"}}})
        raise AssertionError(f"Unexpected URL: {url}")

    report = run_demo_preflight(
        web_url="http://web.test/",
        agent_url="http://agent.test",
        mcp_url="http://mcp.test/mcp/",
        requester=requester,
    )

    checks = _checks_by_id(report)
    assert report.ok is True
    assert checks["endpoint.web"].status == "pass"
    assert checks["endpoint.agent"].status == "pass"
    assert checks["endpoint.mcp"].status == "pass"
    assert [call[0:2] for call in calls] == [
        ("GET", "http://web.test/live"),
        ("GET", "http://web.test/ready"),
        ("GET", "http://agent.test/list-apps"),
        ("POST", "http://mcp.test/mcp"),
    ]
    mcp_request = calls[-1][2]
    assert mcp_request["json"]["method"] == "initialize"
    assert mcp_request["headers"]["Accept"] == "application/json, text/event-stream"


def test_cloud_run_probe_fetches_token_for_service_origin_without_exposing_it():
    audiences = []
    token = "temporary-sensitive-id-token"

    def token_fetcher(audience):
        audiences.append(audience)
        return token

    def requester(method, url, **kwargs):
        assert method == "GET"
        assert url == "https://agent-example.run.app/list-apps"
        assert kwargs["headers"]["Authorization"] == f"Bearer {token}"
        return FakeResponse(["agentic_agriculture"])

    report = run_demo_preflight(
        agent_url="https://agent-example.run.app",
        requester=requester,
        token_fetcher=token_fetcher,
    )

    assert report.ok is True
    assert audiences == ["https://agent-example.run.app"]
    assert token not in json.dumps(report.as_dict())


def test_unready_web_endpoint_is_a_required_failure():
    def requester(method, url, **kwargs):  # noqa: ARG001
        if url.endswith("/live"):
            return FakeResponse({"status": "ok"})
        return FakeResponse(
            {"status": "not_ready", "checks": {"cloud_tasks": False}}, status_code=503
        )

    report = run_demo_preflight(web_url="http://web.test", requester=requester)

    check = _checks_by_id(report)["endpoint.web"]
    assert report.ok is False
    assert check.status == "fail"
    assert "HTTP 503" in check.message


def test_endpoint_with_embedded_credentials_is_rejected_without_request():
    def requester(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("Requester must not be called for an unsafe URL")

    report = run_demo_preflight(
        mcp_url="https://username:password@mcp.example/mcp",
        requester=requester,
    )

    check = _checks_by_id(report)["endpoint.mcp"]
    assert report.ok is False
    assert check.status == "fail"
    assert "username" not in check.message
    assert "password" not in check.message


def test_non_json_endpoint_response_is_a_failure():
    def requester(method, url, **kwargs):  # noqa: ARG001
        return FakeResponse(ValueError("not json"))

    report = run_demo_preflight(agent_url="http://agent.test", requester=requester)

    check = _checks_by_id(report)["endpoint.agent"]
    assert check.status == "fail"
    assert "valid JSON" in check.message


def test_management_command_json_output_is_machine_readable():
    stdout = StringIO()

    call_command("demo_preflight", "--json", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["ok"] is True
    assert payload["environment"] == "development"
    assert payload["summary"]["failures"] == 0
    assert {item["status"] for item in payload["checks"]} <= {"pass", "warning", "fail"}


def test_management_command_prints_human_summary():
    stdout = StringIO()

    call_command("demo_preflight", stdout=stdout)

    output = stdout.getvalue()
    assert "Agentic Agriculture demo preflight (development)" in output
    assert "[PASS] runtime.environment" in output
    assert "[WARN] endpoint.web" in output
    assert "Summary:" in output


def test_management_command_exits_nonzero_on_required_failure():
    stdout = StringIO()

    with pytest.raises(CommandError, match="required failures"):
        call_command("demo_preflight", "--timeout", "0", stdout=stdout)

    output = stdout.getvalue()
    assert "[FAIL] preflight.timeout" in output
    assert "1 failures" in output
