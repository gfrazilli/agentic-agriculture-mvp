import pytest

from agentic_agriculture.config import AgenticConfig


def test_default_model_satisfies_gemini_35_requirement() -> None:
    config = AgenticConfig()

    assert config.model == "gemini-3.5-flash"
    assert config.mcp_url == "http://geospatial-mcp:8090/mcp"


@pytest.mark.parametrize("model", ["gemini-2.5-pro", "gemini-3.0-flash", "gpt-5"])
def test_rejects_models_below_gemini_35(model: str) -> None:
    with pytest.raises(ValueError):
        AgenticConfig(model=model)


def test_accepts_newer_gemini_versions_and_environment_aliases() -> None:
    config = AgenticConfig.from_env(
        {
            "GEMINI_MODEL": "gemini-4.0-flash",
            "MCP_URL": "https://mcp-example.run.app/mcp",
            "MCP_AUDIENCE": "https://mcp-example.run.app",
            "AGENT_MCP_TIMEOUT_SECONDS": "9.5",
            "AGENT_MCP_TOOL_CACHE_SECONDS": "120",
        }
    )

    assert config.model == "gemini-4.0-flash"
    assert config.mcp_audience == "https://mcp-example.run.app"
    assert config.mcp_timeout_seconds == 9.5
    assert config.mcp_tool_cache_seconds == 120.0


def test_rejects_cleartext_remote_mcp_and_url_credentials() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        AgenticConfig(mcp_url="http://example.com/mcp")
    with pytest.raises(ValueError, match="credentials"):
        AgenticConfig(mcp_url="https://user:secret@example.com/mcp")
    with pytest.raises(ValueError, match="port"):
        AgenticConfig(mcp_url="https://example.com:not-a-port/mcp")
    with pytest.raises(ValueError, match="query"):
        AgenticConfig(mcp_url="https://example.com/mcp?token=secret")
    with pytest.raises(ValueError, match="service origin"):
        AgenticConfig(
            mcp_url="https://mcp-example.run.app/mcp",
            mcp_audience="https://mcp-example.run.app/mcp",
        )


def test_rejects_non_finite_timeouts() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        AgenticConfig(mcp_timeout_seconds=float("nan"))


def test_mcp_can_be_explicitly_disabled_for_offline_runtime() -> None:
    config = AgenticConfig.from_env(
        {
            "AGENT_MCP_ENABLED": "false",
            "AGENT_MCP_URL": "not-needed-while-disabled",
        }
    )

    assert config.mcp_enabled is False
