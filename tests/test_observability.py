from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from agriculture.observability import audit_event, get_audit_logger
from geospatial import mcp_server


def _payloads(caplog, logger_name: str) -> list[dict[str, object]]:
    messages = dict.fromkeys(
        record.getMessage() for record in caplog.records if record.name == logger_name
    )
    return [json.loads(message) for message in messages]


def test_audit_logger_drops_free_text_geometry_credentials_and_nested_values(
    caplog, monkeypatch
) -> None:
    logger = get_audit_logger("tests.safe_audit")
    caplog.set_level("INFO", logger=logger.name)
    monkeypatch.setattr(logger, "propagate", True)

    audit_event(
        logger,
        "test.audit",
        execution_id="6a358ec8-92af-4c94-94ef-7c4676ec597e",
        component="test",
        tool_name="get_analysis_evidence",
        status="completed",
        model="Bearer secret-model-value",
        message="minha pergunta privada",
        geometry={"coordinates": [-48.9, -23.9]},
        authorization="Bearer top-secret-token",
        provider_response={"text": "private model answer"},
    )

    payload = _payloads(caplog, logger.name)[0]
    assert payload["event"] == "test.audit"
    assert payload["tool_name"] == "get_analysis_evidence"
    assert "model" not in payload
    assert "message" not in payload
    assert "geometry" not in payload
    assert "authorization" not in payload
    assert "provider_response" not in payload
    assert "minha pergunta privada" not in caplog.text
    assert "top-secret-token" not in caplog.text
    assert "private model answer" not in caplog.text
    assert "-48.9" not in caplog.text


class _SuccessfulTools:
    def plan_observations(self, **kwargs):  # noqa: ARG002
        return {
            "status": "ready",
            "scenes": [{"id": "S2A_SAFE_001"}, {"id": "S2B_SAFE_002"}],
        }


class _FailingTools:
    def search_scenes(self, **kwargs):  # noqa: ARG002
        raise RuntimeError("Bearer private-token geometry=-48.9,-23.9")


def test_mcp_audit_records_tool_and_scene_ids_but_never_arguments(caplog, monkeypatch) -> None:
    caplog.set_level("INFO", logger="geospatial.mcp_server")
    monkeypatch.setattr(logging.getLogger("geospatial.mcp_server"), "propagate", True)
    monkeypatch.setattr(mcp_server, "get_tools", lambda: _SuccessfulTools())

    result = mcp_server.plan_field_observations(
        polygon={
            "type": "Polygon",
            "coordinates": [[[-48.9, -23.9], [-48.8, -23.9], [-48.9, -23.9]]],
        },
        start="2026-01-01",
        end="2026-02-01",
    )

    assert result["status"] == "ready"
    events = _payloads(caplog, "geospatial.mcp_server")
    assert [event["event"] for event in events] == ["mcp.tool.started", "mcp.tool.completed"]
    assert events[0]["execution_id"] == events[1]["execution_id"]
    assert events[1]["tool_name"] == "plan_field_observations"
    assert events[1]["scene_ids"] == ["S2A_SAFE_001", "S2B_SAFE_002"]
    assert "coordinates" not in caplog.text
    assert "-48.9" not in caplog.text
    assert "2026-01-01" not in caplog.text


def test_mcp_failure_does_not_format_exception_message(caplog, monkeypatch) -> None:
    caplog.set_level("INFO", logger="geospatial.mcp_server")
    monkeypatch.setattr(logging.getLogger("geospatial.mcp_server"), "propagate", True)
    monkeypatch.setattr(mcp_server, "get_tools", lambda: _FailingTools())

    with pytest.raises(RuntimeError):
        mcp_server.search_sentinel_scenes(
            west=-48.9,
            south=-23.9,
            east=-48.8,
            north=-23.8,
            start="2026-01-01",
            end="2026-02-01",
        )

    failure = _payloads(caplog, "geospatial.mcp_server")[-1]
    assert failure["event"] == "mcp.tool.failed"
    assert failure["error_type"] == "RuntimeError"
    assert "private-token" not in caplog.text
    assert "-48.9" not in caplog.text


def test_audit_logger_is_info_enabled_and_installs_no_duplicate_handler(monkeypatch) -> None:
    logger = get_audit_logger("tests.audit_configuration")
    managed_handlers = [
        handler
        for handler in logger.handlers
        if getattr(handler, "_agentic_agriculture_audit_handler", False)
    ]
    assert logger.getEffectiveLevel() == logging.INFO
    assert len(managed_handlers) == 1
    assert get_audit_logger(logger.name) is logger
    assert (
        len(
            [
                handler
                for handler in logger.handlers
                if getattr(handler, "_agentic_agriculture_audit_handler", False)
            ]
        )
        == 1
    )

    output = StringIO()
    monkeypatch.setattr(managed_handlers[0], "stream", output)
    audit_event(
        logger,
        "test.single_output",
        execution_id="6a358ec8-92af-4c94-94ef-7c4676ec597e",
        component="test",
        status="completed",
    )

    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "test.single_output"
