"""Factories for the Gemini 3.5+ Google ADK multi-agent application."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from agentic_agriculture.auth import (
    CloudRunIDTokenHeaderProvider,
    IDTokenFetcher,
    fetch_google_id_token,
)
from agentic_agriculture.config import AgenticConfig
from agentic_agriculture.prompts import (
    BOUNDARY_DESCRIPTION,
    BOUNDARY_INSTRUCTION,
    COORDINATOR_INSTRUCTION,
    EXPLAINER_DESCRIPTION,
    EXPLAINER_INSTRUCTION,
    TEMPORAL_ANALYSIS_DESCRIPTION,
    TEMPORAL_ANALYSIS_INSTRUCTION,
)
from agentic_agriculture.tools import (
    AgricultureActionTools,
    ReadOnlyAgricultureTools,
    RepositoryProvider,
    ServiceProvider,
)

BOUNDARY_MCP_TOOLS = ("search_sentinel_scenes", "get_sentinel_scene")
TEMPORAL_MCP_TOOLS = (
    "search_sentinel_scenes",
    "get_sentinel_scene",
    "plan_field_observations",
)


@dataclass(frozen=True, slots=True)
class ADKBindings:
    """Late-loaded ADK classes, injectable so graph construction is unit-testable."""

    Agent: Any
    App: Any
    Gemini: Any
    HttpRetryOptions: Any
    McpToolset: Any
    StreamableHTTPConnectionParams: Any


@dataclass(frozen=True, slots=True)
class AgentGraph:
    """Named references to the runnable ADK application and its specialists."""

    app: Any
    root_agent: Any
    boundary_specialist: Any
    temporal_analysis_specialist: Any
    evidence_explainer: Any
    mcp_toolsets: tuple[Any, ...]


def load_adk_bindings() -> ADKBindings:
    """Import Google ADK only when the runtime graph is explicitly built."""

    try:
        from google.adk.agents import Agent
        from google.adk.apps import App
        from google.adk.models import Gemini
        from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
        from google.genai.types import HttpRetryOptions
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on runtime installation
        raise RuntimeError(
            "Google ADK is not installed. Install the project's ADK/GCP runtime dependencies."
        ) from exc
    return ADKBindings(
        Agent=Agent,
        App=App,
        Gemini=Gemini,
        HttpRetryOptions=HttpRetryOptions,
        McpToolset=McpToolset,
        StreamableHTTPConnectionParams=StreamableHTTPConnectionParams,
    )


def _gemini(bindings: ADKBindings, config: AgenticConfig) -> Any:
    return bindings.Gemini(
        model=config.model,
        retry_options=bindings.HttpRetryOptions(attempts=3),
    )


def _mcp_toolset(
    *,
    bindings: ADKBindings,
    config: AgenticConfig,
    tool_filter: tuple[str, ...],
    token_fetcher: IDTokenFetcher,
) -> Any:
    headers = {"Accept": "application/json, text/event-stream"}
    connection = bindings.StreamableHTTPConnectionParams(
        url=config.mcp_url,
        headers=headers,
        timeout=config.mcp_timeout_seconds,
    )
    header_provider = None
    if config.mcp_audience is not None:
        header_provider = CloudRunIDTokenHeaderProvider(
            config.mcp_audience,
            token_fetcher=token_fetcher,
        )
    return bindings.McpToolset(
        connection_params=connection,
        tool_filter=list(tool_filter),
        tool_list_cache_ttl_seconds=config.mcp_tool_cache_seconds,
        require_confirmation=False,
        header_provider=header_provider,
    )


def build_agent_graph(
    config: AgenticConfig | None = None,
    *,
    repository_provider: RepositoryProvider | None = None,
    service_provider: ServiceProvider | None = None,
    bindings: ADKBindings | None = None,
    token_fetcher: IDTokenFetcher = fetch_google_id_token,
) -> AgentGraph:
    """Build the coordinator and three specialists without calling Gemini or MCP."""

    config = config or AgenticConfig.from_env()
    bindings = bindings or load_adk_bindings()
    read_tools = (
        ReadOnlyAgricultureTools(repository_provider)
        if repository_provider is not None
        else ReadOnlyAgricultureTools()
    )
    action_tools = (
        AgricultureActionTools(service_provider)
        if service_provider is not None
        else AgricultureActionTools()
    )

    boundary_mcp = None
    temporal_mcp = None
    if config.mcp_enabled:
        boundary_mcp = _mcp_toolset(
            bindings=bindings,
            config=config,
            tool_filter=BOUNDARY_MCP_TOOLS,
            token_fetcher=token_fetcher,
        )
        temporal_mcp = _mcp_toolset(
            bindings=bindings,
            config=config,
            tool_filter=TEMPORAL_MCP_TOOLS,
            token_fetcher=token_fetcher,
        )

    boundary_tools: list[Any] = [read_tools.get_field_context]
    temporal_tools: list[Any] = [
        read_tools.get_field_context,
        action_tools.request_field_analysis,
        read_tools.get_analysis_evidence,
        read_tools.list_field_analyses,
    ]
    if boundary_mcp is not None:
        boundary_tools.append(boundary_mcp)
    if temporal_mcp is not None:
        temporal_tools.append(temporal_mcp)

    boundary_specialist = bindings.Agent(
        name="boundary_specialist",
        model=_gemini(bindings, config),
        description=BOUNDARY_DESCRIPTION,
        instruction=BOUNDARY_INSTRUCTION,
        tools=boundary_tools,
    )
    temporal_analysis_specialist = bindings.Agent(
        name="temporal_analysis_specialist",
        model=_gemini(bindings, config),
        description=TEMPORAL_ANALYSIS_DESCRIPTION,
        instruction=TEMPORAL_ANALYSIS_INSTRUCTION,
        tools=temporal_tools,
    )
    evidence_explainer = bindings.Agent(
        name="evidence_explainer",
        model=_gemini(bindings, config),
        description=EXPLAINER_DESCRIPTION,
        instruction=EXPLAINER_INSTRUCTION,
        tools=[read_tools.get_analysis_evidence, read_tools.get_zone_evidence],
    )
    root_agent = bindings.Agent(
        name="agriculture_coordinator",
        model=_gemini(bindings, config),
        description="Coordena a conversa e delega cada intenção ao especialista adequado.",
        instruction=COORDINATOR_INSTRUCTION,
        sub_agents=[
            boundary_specialist,
            temporal_analysis_specialist,
            evidence_explainer,
        ],
    )
    app = bindings.App(name=config.app_name, root_agent=root_agent)
    return AgentGraph(
        app=app,
        root_agent=root_agent,
        boundary_specialist=boundary_specialist,
        temporal_analysis_specialist=temporal_analysis_specialist,
        evidence_explainer=evidence_explainer,
        mcp_toolsets=tuple(item for item in (boundary_mcp, temporal_mcp) if item is not None),
    )


def build_app(config: AgenticConfig | None = None, **kwargs: Any) -> Any:
    """Build and return the ADK ``App`` for custom ASGI/Cloud Run entry points."""

    return build_agent_graph(config, **kwargs).app


@lru_cache(maxsize=1)
def get_runtime_graph() -> AgentGraph:
    """Create the process-wide graph when an ADK loader asks for its entry point."""

    return build_agent_graph()


def __getattr__(name: str) -> Any:
    """Expose conventional ADK names lazily, keeping ordinary imports offline-safe."""

    if name == "root_agent":
        return get_runtime_graph().root_agent
    if name == "app":
        return get_runtime_graph().app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), "app", "root_agent"])
