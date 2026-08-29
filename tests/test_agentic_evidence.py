from uuid import uuid4

from agentic_agriculture.evidence import (
    INTERPRETATION_LIMITS_PT,
    analysis_evidence,
    field_evidence,
    zone_evidence,
)
from agentic_agriculture.tools import ReadOnlyAgricultureTools
from agriculture.adapters.memory import InMemoryAgricultureRepository
from agriculture.fixture_loader import load_fixture
from agriculture.schemas import Analysis, Field


def _fixtures() -> tuple[Field, Analysis, Analysis]:
    field = load_fixture("field-draft")
    completed = load_fixture("analysis-result")
    running = load_fixture("analysis-running")
    assert isinstance(field, Field)
    assert isinstance(completed, Analysis)
    assert isinstance(running, Analysis)
    return field, completed, running


def test_evidence_projection_is_grounded_and_non_diagnostic() -> None:
    field, completed, _running = _fixtures()

    field_payload = field_evidence(field)
    analysis_payload = analysis_evidence(completed)

    assert field_payload["field"]["boundary_confirmed"] is False
    assert field_payload["field"]["boundary"] is None
    assert analysis_payload["result_available"] is True
    assert analysis_payload["result"]["provenance"]["mission"] == "Sentinel-2"
    assert analysis_payload["result"]["provenance"]["bands"] == ["B04", "B05", "B08", "B11"]
    assert len(analysis_payload["result"]["scenes"]) == 3
    assert len(analysis_payload["result"]["zones"]) == 4
    assert tuple(analysis_payload["interpretation_limits_pt"]) == INTERPRETATION_LIMITS_PT
    assert "boundary" not in analysis_payload["result"]["zones"][0]


def test_unfinished_analysis_does_not_claim_a_result() -> None:
    _field, _completed, running = _fixtures()

    payload = analysis_evidence(running)

    assert payload["analysis"]["status"] == "running"
    assert payload["result_available"] is False
    assert "result" not in payload


def test_zone_projection_includes_exact_geometry_and_trajectory() -> None:
    _field, completed, _running = _fixtures()

    payload = zone_evidence(completed, "zone-1")

    assert payload is not None
    assert payload["zone"]["zone_id"] == "zone-1"
    assert payload["zone"]["boundary"]["type"] == "Polygon"
    assert len(payload["zone"]["trajectory"]) == 3
    assert zone_evidence(completed, "zone-7") is None


def test_repository_tools_are_read_only_and_return_stable_errors() -> None:
    field, completed, running = _fixtures()
    running = running.model_copy(update={"id": uuid4()})
    repository = InMemoryAgricultureRepository()
    repository.save_field(field)
    repository.save_analysis(completed)
    repository.save_analysis(running)
    tools = ReadOnlyAgricultureTools(lambda: repository)

    field_result = tools.get_field_context(str(field.id))
    analysis_result = tools.get_analysis_evidence(str(completed.id))
    zone_result = tools.get_zone_evidence(str(completed.id), "zone-2")
    recent_result = tools.list_field_analyses(str(field.id), limit=1)

    assert field_result["ok"] is True
    assert analysis_result["ok"] is True
    assert zone_result["ok"] is True
    assert recent_result["evidence"]["count"] == 1
    assert recent_result["evidence"]["analyses"][0]["status"] == "completed"
    assert tools.get_field_context(" ")["error"]["code"] == "invalid_field_id"
    assert tools.get_analysis_evidence("missing")["error"]["code"] == "analysis_not_found"
    assert tools.get_zone_evidence(str(completed.id), "zone-99")["error"]["code"] == (
        "invalid_zone_id"
    )
    assert tools.list_field_analyses(str(field.id), limit=21)["error"]["code"] == "invalid_limit"

    assert len(repository.list_fields()) == 1
    assert len(repository.list_analyses()) == 2
