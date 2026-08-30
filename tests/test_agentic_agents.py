from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from agentic_agriculture import AgenticConfig
from agentic_agriculture import agent as agent_module
from agentic_agriculture.agent import ADKBindings, build_agent_graph
from agentic_agriculture.auth import CloudRunIDTokenHeaderProvider


class FakeComponent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


def _bindings() -> ADKBindings:
    return ADKBindings(
        Agent=FakeComponent,
        App=FakeComponent,
        Gemini=FakeComponent,
        HttpRetryOptions=FakeComponent,
        McpToolset=FakeComponent,
        StreamableHTTPConnectionParams=FakeComponent,
    )


def test_package_import_does_not_import_google_adk() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import sys; import agentic_agriculture; print(int('google.adk' in sys.modules))"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "0"


def test_default_repository_tool_bootstraps_django_in_adk_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "os.environ.pop('DJANGO_SETTINGS_MODULE', None); "
                "from agentic_agriculture.tools import ReadOnlyAgricultureTools; "
                "result = ReadOnlyAgricultureTools().get_field_context('missing'); "
                "print(result['error']['code'])"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "field_not_found"


def test_conventional_adk_exports_are_resolved_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_graph = SimpleNamespace(root_agent="root", app="application")
    monkeypatch.setattr(agent_module, "get_runtime_graph", lambda: fake_graph)

    assert "root_agent" in dir(agent_module)
    assert "app" in dir(agent_module)
    assert agent_module.__getattr__("root_agent") == "root"
    assert agent_module.__getattr__("app") == "application"


def test_builds_multi_agent_gemini_graph_without_network_calls() -> None:
    token_calls: list[str] = []

    def token_fetcher(audience: str) -> str:
        token_calls.append(audience)
        return "signed-token"

    config = AgenticConfig(
        model="gemini-3.5-pro",
        mcp_url="https://mcp-example.run.app/mcp",
        mcp_audience="https://mcp-example.run.app",
    )
    graph = build_agent_graph(
        config,
        bindings=_bindings(),
        repository_provider=lambda: None,  # type: ignore[arg-type,return-value]
        token_fetcher=token_fetcher,
    )

    assert graph.app.name == "agentic_agriculture"
    assert graph.app.root_agent is graph.root_agent
    assert [agent.name for agent in graph.root_agent.sub_agents] == [
        "boundary_specialist",
        "temporal_analysis_specialist",
        "evidence_explainer",
    ]
    assert graph.root_agent.model.model == "gemini-3.5-pro"
    assert len(graph.mcp_toolsets) == 2
    assert token_calls == []

    for specialist in graph.root_agent.sub_agents:
        prompt = specialist.instruction(SimpleNamespace(state={"language": "pt-BR"})).lower()
        assert "nunca diagnostique" in prompt
        assert "português do brasil" in prompt
        assert "somente texto simples" in prompt
        assert "no máximo 180 palavras" in prompt
    explainer_prompt = graph.evidence_explainer.instruction(
        SimpleNamespace(state={"language": "pt-BR"})
    )
    assert "fora da visão humana" in explainer_prompt
    assert "pipeline determinístico" in explainer_prompt

    boundary_mcp, temporal_mcp = graph.mcp_toolsets
    assert boundary_mcp.connection_params.url == "https://mcp-example.run.app/mcp"
    assert boundary_mcp.tool_filter == ["search_sentinel_scenes", "get_sentinel_scene"]
    assert temporal_mcp.tool_filter == [
        "search_sentinel_scenes",
        "get_sentinel_scene",
        "plan_field_observations",
    ]
    assert "request_field_analysis" in {
        tool.__name__ for tool in graph.temporal_analysis_specialist.tools if callable(tool)
    }
    temporal_prompt = graph.temporal_analysis_specialist.instruction(
        SimpleNamespace(state={"language": "pt-BR"})
    )
    assert "get_analysis_evidence" in temporal_prompt
    assert boundary_mcp.connection_params.headers == {
        "Accept": "application/json, text/event-stream"
    }

    assert isinstance(boundary_mcp.header_provider, CloudRunIDTokenHeaderProvider)
    assert boundary_mcp.header_provider(None) == {"Authorization": "Bearer signed-token"}
    assert token_calls == ["https://mcp-example.run.app"]


def test_every_agent_binds_response_contract_to_trusted_session_language() -> None:
    graph = build_agent_graph(
        AgenticConfig(mcp_enabled=False),
        bindings=_bindings(),
        repository_provider=lambda: None,  # type: ignore[arg-type,return-value]
    )
    agents = (graph.root_agent, *graph.root_agent.sub_agents)

    for agent in agents:
        english = agent.instruction(SimpleNamespace(state={"language": "en"}))
        assert "Respond exclusively in English" in english
        assert "plain text only" in english
        assert "no more than 180 words" in english
        assert "Responda exclusivamente em português" not in english

        portuguese = agent.instruction(SimpleNamespace(state={"language": "pt-BR"}))
        assert "Responda exclusivamente em português do Brasil" in portuguese
        assert "somente texto simples" in portuguese
        assert "no máximo 180 palavras" in portuguese


@pytest.mark.parametrize("state", [{}, {"language": "es"}, {"language": None}])
def test_agent_instruction_fails_closed_without_supported_session_language(
    state: dict[str, object],
) -> None:
    graph = build_agent_graph(
        AgenticConfig(mcp_enabled=False),
        bindings=_bindings(),
        repository_provider=lambda: None,  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(ValueError, match="pt-BR.*en"):
        graph.root_agent.instruction(SimpleNamespace(state=state))


def test_mcp_can_be_disabled_while_preserving_all_specialists() -> None:
    graph = build_agent_graph(
        AgenticConfig(mcp_enabled=False),
        bindings=_bindings(),
        repository_provider=lambda: None,  # type: ignore[arg-type,return-value]
    )

    assert graph.mcp_toolsets == ()
    assert len(graph.root_agent.sub_agents) == 3
    assert len(graph.boundary_specialist.tools) == 1
    assert len(graph.temporal_analysis_specialist.tools) == 4
    assert [tool.__name__ for tool in graph.temporal_analysis_specialist.tools] == [
        "get_field_context",
        "request_field_analysis",
        "get_analysis_evidence",
        "list_field_analyses",
    ]


def test_installed_google_adk_builds_the_real_graph_without_external_calls() -> None:
    graph = build_agent_graph(AgenticConfig(mcp_enabled=False))

    assert type(graph.app).__name__ == "App"
    assert graph.root_agent.name == "agriculture_coordinator"
    assert [agent.name for agent in graph.root_agent.sub_agents] == [
        "boundary_specialist",
        "temporal_analysis_specialist",
        "evidence_explainer",
    ]


def test_cloud_run_header_provider_is_lazy_cached_and_refreshable() -> None:
    clock = [100.0]
    calls: list[str] = []

    def fetch(audience: str) -> str:
        calls.append(audience)
        return f"token-{len(calls)}"

    provider = CloudRunIDTokenHeaderProvider(
        "https://mcp-example.run.app",
        token_fetcher=fetch,
        clock=lambda: clock[0],
        cache_seconds=60,
    )

    assert calls == []
    assert provider(None) == {"Authorization": "Bearer token-1"}
    assert provider(None) == {"Authorization": "Bearer token-1"}
    clock[0] = 161.0
    assert provider(None) == {"Authorization": "Bearer token-2"}
    assert calls == ["https://mcp-example.run.app", "https://mcp-example.run.app"]
