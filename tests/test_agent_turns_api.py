from __future__ import annotations

import json
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import Client
from django.urls import reverse

from agriculture.adapters import (
    AgentAPIConfigurationError,
    AgentAPIProtocolError,
    AgentAPIUnavailableError,
    AgentTurnReply,
)
from agriculture.api.models import AgentSessionPatchInput
from agriculture.container import get_agriculture_service, reset_container
from agriculture.schemas import AgentSessionStatus
from tests.conftest import TEST_PASSWORD, TEST_USERNAME


@pytest.fixture(autouse=True)
def isolated_container():
    reset_container()
    yield
    reset_container()


@pytest.fixture
def authenticated_client(client: Client) -> Client:
    response = client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 302
    return client


def _post_json(client: Client, url: str, payload: dict[str, object], *, key: str | None = None):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def _create_field(client: Client, *, suffix: str = "turn") -> dict[str, object]:
    response = _post_json(
        client,
        reverse("agriculture_api:fields"),
        {
            "name": "Talhão da conversa",
            "crop": "soja",
            "season_start": "2025-10-15",
            "season_end": "2026-03-10",
            "estimated_area_ha": 12.4,
            "reference_location": {
                "type": "Point",
                "coordinates": [-48.9029, -23.9786],
            },
        },
        key=f"field-{suffix}-0001",
    )
    assert response.status_code == 201, response.content
    return response.json()["data"]


def _create_session(
    client: Client,
    *,
    field_id: str | None = None,
    analysis_id: str | None = None,
    suffix: str = "turn",
    language: str = "pt-BR",
) -> dict[str, object]:
    payload: dict[str, object] = {"language": language, "channel": "text"}
    if field_id is not None:
        payload["field_id"] = field_id
    if analysis_id is not None:
        payload["analysis_id"] = analysis_id
    response = _post_json(
        client,
        reverse("agriculture_api:agent-sessions"),
        payload,
        key=f"session-{suffix}-0001",
    )
    assert response.status_code == 201, response.content
    return response.json()["data"]


def _fake_gateway(*, exception: Exception | None = None):
    calls: list[tuple[str, object]] = []

    def run_turn(message, context):
        calls.append((message, context))
        if exception is not None:
            raise exception
        return AgentTurnReply(
            text="<strong>Resumo:</strong> a zona 2 está relativamente abaixo.",
            model="gemini-3.5-flash-001",
            agents=("agriculture_coordinator", "evidence_explainer"),
            tools=("get_analysis_evidence",),
        )

    return SimpleNamespace(run_turn=run_turn), calls


def test_turn_endpoint_uses_trusted_context_and_increments_only_after_success(
    authenticated_client: Client,
    monkeypatch,
    caplog,
) -> None:
    caplog.set_level("INFO", logger="agriculture.api.views")
    monkeypatch.setattr(logging.getLogger("agriculture.api.views"), "propagate", True)
    field = _create_field(authenticated_client)
    session = _create_session(authenticated_client, field_id=str(field["id"]))
    gateway, calls = _fake_gateway()
    monkeypatch.setattr("agriculture.api.views.get_agent_api_client", lambda: gateway)

    response = _post_json(
        authenticated_client,
        reverse("agriculture_api:agent-session-turns", args=[session["id"]]),
        {"message": "O que significa a zona 2?"},
    )

    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["message"] == {
        "role": "assistant",
        "text": "Resumo: a zona 2 está relativamente abaixo.",
        "format": "plain_text",
    }
    assert data["session"]["turn_count"] == 1
    assert data["trace"] == {
        "provider": "Google Vertex AI",
        "framework": "Google ADK",
        "model": "gemini-3.5-flash-001",
        "agents": ["agriculture_coordinator", "evidence_explainer"],
        "tools": ["get_analysis_evidence"],
    }
    assert len(calls) == 1
    message, context = calls[0]
    assert message == "O que significa a zona 2?"
    assert context.session_id == session["id"]
    assert isinstance(context.actor_id, str) and len(context.actor_id) >= 16
    assert context.actor_id != TEST_USERNAME
    assert context.field_id == field["id"]

    audit_messages = dict.fromkeys(
        record.getMessage() for record in caplog.records if record.name == "agriculture.api.views"
    )
    audit_events = [json.loads(message) for message in audit_messages]
    started, completed = [
        event for event in audit_events if event["event"].startswith("agent_gateway.turn.")
    ]
    assert started["event"] == "agent_gateway.turn.started"
    assert completed["event"] == "agent_gateway.turn.completed"
    assert started["execution_id"] == completed["execution_id"] == context.execution_id
    assert completed["session_id"] == session["id"]
    assert completed["field_id"] == field["id"]
    assert completed["turn_number"] == 1
    assert completed["tools"] == ["get_analysis_evidence"]
    assert "O que significa a zona 2?" not in caplog.text
    assert "Resumo:" not in caplog.text

    stored = authenticated_client.get(
        reverse("agriculture_api:agent-session-detail", args=[session["id"]])
    )
    assert stored.json()["data"]["turn_count"] == 1


def test_analysis_only_session_derives_and_binds_its_field(
    authenticated_client: Client,
    monkeypatch,
) -> None:
    field = _create_field(authenticated_client, suffix="analysis-context")
    suggestion = _post_json(
        authenticated_client,
        reverse("agriculture_api:boundary-suggestion", args=[field["id"]]),
        {},
        key="boundary-analysis-context-0001",
    ).json()["data"]
    confirmation = authenticated_client.patch(
        reverse("agriculture_api:field-detail", args=[field["id"]]),
        data=json.dumps({"boundary": suggestion["boundary"], "boundary_confirmed": True}),
        content_type="application/json",
    )
    assert confirmation.status_code == 200
    analysis_response = _post_json(
        authenticated_client,
        reverse("agriculture_api:analyses"),
        {"field_id": field["id"], "requested_zone_count": 4},
        key="analysis-context-0001",
    )
    assert analysis_response.status_code == 202
    analysis = analysis_response.json()["data"]
    session = _create_session(
        authenticated_client,
        analysis_id=str(analysis["id"]),
        suffix="analysis-context",
    )
    gateway, calls = _fake_gateway()
    monkeypatch.setattr("agriculture.api.views.get_agent_api_client", lambda: gateway)

    response = _post_json(
        authenticated_client,
        reverse("agriculture_api:agent-session-turns", args=[session["id"]]),
        {"message": "Explique o resultado."},
    )

    assert response.status_code == 200
    context = calls[0][1]
    assert context.field_id == field["id"]
    assert context.analysis_id == analysis["id"]


def test_turn_passes_persisted_english_language_to_private_agent(
    authenticated_client: Client,
    monkeypatch,
) -> None:
    session = _create_session(
        authenticated_client,
        suffix="english-context",
        language="en",
    )
    gateway, calls = _fake_gateway()
    monkeypatch.setattr("agriculture.api.views.get_agent_api_client", lambda: gateway)

    response = _post_json(
        authenticated_client,
        reverse("agriculture_api:agent-session-turns", args=[session["id"]]),
        {"message": "Which zone should I inspect first?"},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    _, context = calls[0]
    assert context.language == "en"
    assert context.channel == "text"


@pytest.mark.parametrize(
    ("message", "expected_status"),
    [
        ("", 422),
        ("x" * 2_001, 422),
        ("Veja <script>alert(1)</script>", 422),
        ("controle\u0000inválido", 422),
    ],
)
def test_turn_input_is_bounded_plain_text(
    authenticated_client: Client,
    monkeypatch,
    message: str,
    expected_status: int,
) -> None:
    session = _create_session(authenticated_client, suffix=f"validation-{len(message)}")
    gateway, calls = _fake_gateway()
    monkeypatch.setattr("agriculture.api.views.get_agent_api_client", lambda: gateway)

    response = _post_json(
        authenticated_client,
        reverse("agriculture_api:agent-session-turns", args=[session["id"]]),
        {"message": message},
    )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == "validation_error"
    assert calls == []


@pytest.mark.parametrize(
    ("exception", "status", "code"),
    [
        (
            AgentAPIConfigurationError("secret configuration detail"),
            503,
            "agent_unavailable",
        ),
        (AgentAPIUnavailableError("private URL and token detail"), 503, "agent_unavailable"),
        (AgentAPIProtocolError("private response detail"), 502, "agent_invalid_response"),
    ],
)
def test_gateway_failures_are_safe_and_do_not_increment_turn_count(
    authenticated_client: Client,
    monkeypatch,
    caplog,
    exception: Exception,
    status: int,
    code: str,
) -> None:
    caplog.set_level("INFO", logger="agriculture.api.views")
    monkeypatch.setattr(logging.getLogger("agriculture.api.views"), "propagate", True)
    session = _create_session(authenticated_client, suffix=f"failure-{status}-{code}")
    gateway, calls = _fake_gateway(exception=exception)
    monkeypatch.setattr("agriculture.api.views.get_agent_api_client", lambda: gateway)

    response = _post_json(
        authenticated_client,
        reverse("agriculture_api:agent-session-turns", args=[session["id"]]),
        {"message": "Explique."},
    )

    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert "secret" not in error["message"]
    assert "token" not in error["message"]
    assert len(calls) == 1
    failure = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if "agent_gateway.turn.failed" in record.getMessage()
    )
    assert failure["error_type"] == type(exception).__name__
    assert failure["error_code"] == code
    assert "secret configuration detail" not in caplog.text
    assert "private URL and token detail" not in caplog.text
    assert "private response detail" not in caplog.text
    assert "Explique." not in caplog.text
    stored = authenticated_client.get(
        reverse("agriculture_api:agent-session-detail", args=[session["id"]])
    )
    assert stored.json()["data"]["turn_count"] == 0


def test_expired_or_completed_session_never_calls_private_agent(
    authenticated_client: Client,
    monkeypatch,
) -> None:
    expired = _create_session(authenticated_client, suffix="expired")
    completed = _create_session(authenticated_client, suffix="completed")
    service = get_agriculture_service()
    expired_model = service.get_agent_session(expired["id"])
    expired_model = expired_model.model_copy(
        update={
            "started_at": expired_model.started_at - timedelta(hours=2),
            "updated_at": expired_model.updated_at - timedelta(hours=2),
            "expires_at": expired_model.expires_at - timedelta(hours=2),
        }
    )
    service.repository.save_agent_session(expired_model)
    service.patch_agent_session(
        completed["id"],
        AgentSessionPatchInput(status=AgentSessionStatus.COMPLETED),
    )
    gateway, calls = _fake_gateway()
    monkeypatch.setattr("agriculture.api.views.get_agent_api_client", lambda: gateway)

    expired_response = _post_json(
        authenticated_client,
        reverse("agriculture_api:agent-session-turns", args=[expired["id"]]),
        {"message": "Explique."},
    )
    completed_response = _post_json(
        authenticated_client,
        reverse("agriculture_api:agent-session-turns", args=[completed["id"]]),
        {"message": "Explique."},
    )

    assert expired_response.status_code == 410
    assert expired_response.json()["error"]["code"] == "agent_session_expired"
    assert completed_response.status_code == 409
    assert completed_response.json()["error"]["code"] == "agent_session_not_active"
    assert calls == []


def test_anonymous_turn_is_rejected_before_gateway(client: Client, monkeypatch) -> None:
    gateway, calls = _fake_gateway()
    monkeypatch.setattr("agriculture.api.views.get_agent_api_client", lambda: gateway)

    response = _post_json(
        client,
        "/api/v1/agent-sessions/61b9320f-f798-432f-ab17-f5ba36c084a1/turns/",
        {"message": "Explique."},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert calls == []
