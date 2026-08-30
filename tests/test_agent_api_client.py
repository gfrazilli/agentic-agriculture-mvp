from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agriculture.adapters.agent_api import (
    AgentAPIClient,
    AgentAPIConfig,
    AgentAPIConfigurationError,
    AgentAPIProtocolError,
    AgentAPIUnavailableError,
    AgentTurnContext,
)

SESSION_ID = "61b9320f-f798-432f-ab17-f5ba36c084a1"
FIELD_ID = "a9046858-0202-4244-8723-ad94be3da692"
ANALYSIS_ID = "bcdf40a5-bb82-4c5c-aa0b-2e91cd757538"
EXECUTION_ID = "6a358ec8-92af-4c94-94ef-7c4676ec597e"


def _context() -> AgentTurnContext:
    return AgentTurnContext(
        execution_id=EXECUTION_ID,
        session_id=SESSION_ID,
        actor_id="demo-user",
        language="pt-BR",
        channel="text",
        field_id=FIELD_ID,
        analysis_id=ANALYSIS_ID,
    )


def _final_events() -> list[dict[str, Any]]:
    return [
        {
            "author": "temporal_analysis_specialist",
            "content": {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "name": "get_analysis_evidence",
                            "args": {"analysis_id": ANALYSIS_ID},
                        }
                    }
                ],
            },
        },
        {
            "author": "temporal_analysis_specialist",
            "content": {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "get_analysis_evidence",
                            "response": {"ok": True},
                        }
                    }
                ],
            },
        },
        {
            "author": "evidence_explainer",
            "modelVersion": "gemini-3.5-flash-001",
            "content": {
                "role": "model",
                "parts": [{"text": "A zona 2 teve desenvolvimento relativo menor."}],
            },
            "partial": False,
        },
    ]


def test_client_creates_session_authenticates_and_runs_exact_adk_protocol() -> None:
    requests: list[httpx.Request] = []
    token_audiences: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={"detail": "Session not found"})
        if request.url.path.endswith("/sessions"):
            return httpx.Response(200, json={"id": SESSION_ID})
        if request.url.path == "/run":
            return httpx.Response(200, json=_final_events())
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def fetch_token(audience: str) -> str:
        token_audiences.append(audience)
        return "signed-id-token"

    config = AgentAPIConfig(
        base_url="https://agent-example.run.app/",
        audience="https://agent-example.run.app",
        timeout_seconds=30,
    )
    with AgentAPIClient(
        config,
        token_fetcher=fetch_token,
        transport=httpx.MockTransport(handler),
    ) as client:
        reply = client.run_turn("Explique a zona 2.", _context())

    assert [request.method for request in requests] == ["GET", "POST", "POST"]
    assert requests[0].url.path.endswith(
        f"/users/web-cebf292c038fdcd2de5f7ac62c3b81bc/sessions/{SESSION_ID}"
    )
    assert all(request.headers["Authorization"] == "Bearer signed-id-token" for request in requests)
    assert token_audiences == ["https://agent-example.run.app"]

    create_payload = json.loads(requests[1].content)
    assert create_payload == {
        "session_id": SESSION_ID,
        "state": {
            "language": "pt-BR",
            "channel": "text",
            "field_id": FIELD_ID,
            "analysis_id": ANALYSIS_ID,
        },
    }
    run_payload = json.loads(requests[2].content)
    assert run_payload["app_name"] == "agentic_agriculture"
    assert run_payload["session_id"] == SESSION_ID
    assert run_payload["custom_metadata"] == {
        "channel": "django-web",
        "execution_id": EXECUTION_ID,
    }
    assert run_payload["streaming"] is False
    assert run_payload["state_delta"] == create_payload["state"]
    assert run_payload["new_message"]["role"] == "user"
    assert FIELD_ID in run_payload["new_message"]["parts"][0]["text"]
    assert run_payload["new_message"]["parts"][1] == {"text": "Explique a zona 2."}
    assert reply.text == "A zona 2 teve desenvolvimento relativo menor."
    assert reply.model == "gemini-3.5-flash-001"
    assert reply.agents == ("temporal_analysis_specialist", "evidence_explainer")
    assert reply.tools == ("get_analysis_evidence",)


def test_client_reuses_existing_session_without_creating_it() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"id": SESSION_ID})
        return httpx.Response(200, json=_final_events())

    with AgentAPIClient(
        AgentAPIConfig(base_url="http://agent:8080"),
        transport=httpx.MockTransport(handler),
    ) as client:
        client.run_turn("Como está a área?", _context())

    assert methods == ["GET", "POST"]


def test_client_propagates_english_contract_in_state_and_turn_context() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": SESSION_ID})
        return httpx.Response(200, json=_final_events())

    context = AgentTurnContext(
        execution_id=EXECUTION_ID,
        session_id=SESSION_ID,
        actor_id="demo-user",
        language="en",
        channel="text",
        field_id=FIELD_ID,
        analysis_id=ANALYSIS_ID,
    )
    with AgentAPIClient(
        AgentAPIConfig(base_url="http://agent:8080"),
        transport=httpx.MockTransport(handler),
    ) as client:
        client.run_turn("Which zone should I inspect first?", context)

    run_payload = json.loads(requests[-1].content)
    assert run_payload["state_delta"]["language"] == "en"
    trusted_context = run_payload["new_message"]["parts"][0]["text"]
    assert "Respond exclusively in English" in trusted_context
    assert "plain text only" in trusted_context
    assert "no more than 180 words" in trusted_context
    assert "responda exclusivamente" not in trusted_context.lower()
    assert run_payload["new_message"]["parts"][1] == {"text": "Which zone should I inspect first?"}


def test_turn_context_rejects_an_unsupported_language() -> None:
    with pytest.raises(AgentAPIConfigurationError, match="pt-BR.*en"):
        AgentTurnContext(
            execution_id=EXECUTION_ID,
            session_id=SESSION_ID,
            actor_id="demo-user",
            language="es",  # type: ignore[arg-type]
            channel="text",
        )


def test_client_recovers_from_a_concurrent_session_create() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(404)
        if calls == 2:
            return httpx.Response(409, json={"detail": "already exists"})
        if calls == 3:
            return httpx.Response(200, json={"id": SESSION_ID})
        return httpx.Response(200, json=_final_events())

    with AgentAPIClient(
        AgentAPIConfig(base_url="http://agent:8080"),
        transport=httpx.MockTransport(handler),
    ) as client:
        reply = client.run_turn("Explique.", _context())

    assert calls == 4
    assert reply.text.startswith("A zona 2")


def test_client_normalizes_common_markdown_into_plain_text() -> None:
    events = [
        {
            "author": "evidence_explainer",
            "content": {
                "role": "model",
                "parts": [
                    {
                        "text": (
                            "### Summary\n\n**Zone 2** needs inspection.\n"
                            "* Evidence is [available](https://example.test/evidence).\n"
                            "2025. Observation: 0.72\n"
                            "10) NDVI remains evidence.\n"
                            "https://example.test/_private_/scene\n"
                            "ABC__DEF__GHI\n---"
                        )
                    }
                ],
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": SESSION_ID})
        return httpx.Response(200, json=events)

    with AgentAPIClient(
        AgentAPIConfig(base_url="http://agent:8080"),
        transport=httpx.MockTransport(handler),
    ) as client:
        reply = client.run_turn("Explain.", _context())

    assert reply.text == (
        "Summary\n\nZone 2 needs inspection.\n"
        "Evidence is available (https://example.test/evidence).\n"
        "2025. Observation: 0.72\n"
        "10) NDVI remains evidence.\n"
        "https://example.test/_private_/scene\n"
        "ABC__DEF__GHI"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": "not-a-url"},
        {"base_url": "http://public.example"},
        {"base_url": "https://agent.run.app/path", "audience": "https://agent.run.app"},
        {"base_url": "https://agent.run.app"},
        {
            "base_url": "https://agent.run.app",
            "audience": "https://other.run.app",
        },
        {"base_url": "http://agent:8080", "timeout_seconds": 0},
    ],
)
def test_config_rejects_unsafe_service_settings(kwargs: dict[str, Any]) -> None:
    with pytest.raises(AgentAPIConfigurationError):
        AgentAPIConfig(**kwargs)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"not": "events"}),
        httpx.Response(200, json=[]),
        httpx.Response(200, json=[{"author": "agent", "content": {"parts": []}}]),
    ],
)
def test_client_rejects_invalid_adk_run_responses(response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": SESSION_ID})
        return response

    with AgentAPIClient(
        AgentAPIConfig(base_url="http://agent:8080"),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AgentAPIProtocolError):
            client.run_turn("Explique.", _context())


def test_client_maps_network_and_remote_failures_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(SESSION_ID):
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(500)

    with AgentAPIClient(
        AgentAPIConfig(base_url="http://agent:8080"),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AgentAPIUnavailableError, match="ConnectError"):
            client.run_turn("Explique.", _context())
