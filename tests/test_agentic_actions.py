from __future__ import annotations

from datetime import timedelta

from agentic_agriculture.tools import AgricultureActionTools
from agriculture.adapters import InMemoryAgricultureRepository, InMemoryTaskQueue
from agriculture.fixture_loader import load_fixture
from agriculture.schemas import BoundarySuggestion, Field
from agriculture.services.application import AgricultureService


def _field(*, confirmed: bool) -> Field:
    field = load_fixture("field-draft")
    assert isinstance(field, Field)
    if not confirmed:
        return field
    suggestion = load_fixture("boundary-suggestion")
    assert isinstance(suggestion, BoundarySuggestion)
    return Field.model_validate(
        {
            **field.model_dump(),
            "boundary": suggestion.boundary.model_dump(),
            "boundary_confirmed": True,
        }
    )


def _action_tool(
    *, confirmed: bool
) -> tuple[
    AgricultureActionTools,
    InMemoryAgricultureRepository,
    InMemoryTaskQueue,
    Field,
]:
    field = _field(confirmed=confirmed)
    now = field.updated_at + timedelta(seconds=1)
    repository = InMemoryAgricultureRepository(clock=lambda: now)
    queue = InMemoryTaskQueue(clock=lambda: now)
    repository.save_field(field)
    service = AgricultureService(repository, queue, clock=lambda: now)
    return AgricultureActionTools(lambda: service), repository, queue, field


def test_request_field_analysis_enqueues_once_and_replays_same_safe_result(settings) -> None:
    settings.ANALYSIS_DAILY_LIMIT = 3
    tools, repository, queue, field = _action_tool(confirmed=True)

    first = tools.request_field_analysis(str(field.id), 4)
    replay = tools.request_field_analysis(str(field.id), 4)

    assert first == {
        "ok": True,
        "action": "analysis_requested",
        "analysis_id": first["analysis_id"],
        "field_id": str(field.id),
        "status": "queued",
        "replayed": False,
        "follow_up": {
            "tool": "get_analysis_evidence",
            "analysis_id": first["analysis_id"],
        },
    }
    assert replay == {**first, "replayed": True}
    assert len(queue.tasks) == 1
    assert len(repository.list_analyses(str(field.id))) == 1


def test_request_field_analysis_uses_service_boundary_guard(settings) -> None:
    settings.ANALYSIS_DAILY_LIMIT = 3
    tools, repository, queue, field = _action_tool(confirmed=False)

    result = tools.request_field_analysis(str(field.id), 4)

    assert result["ok"] is False
    assert result["error"]["code"] == "field_boundary_not_confirmed"
    assert result["error"]["status"] == 409
    assert queue.tasks == []
    assert repository.list_analyses(str(field.id)) == []


def test_field_update_creates_a_new_deterministic_action_context(settings) -> None:
    settings.ANALYSIS_DAILY_LIMIT = 3
    tools, repository, queue, field = _action_tool(confirmed=True)

    first = tools.request_field_analysis(str(field.id), 4)
    changed = Field.model_validate(
        {
            **field.model_dump(),
            "updated_at": field.updated_at + timedelta(minutes=1),
        }
    )
    repository.save_field(changed)
    second = tools.request_field_analysis(str(field.id), 4)

    assert first["analysis_id"] != second["analysis_id"]
    assert len(queue.tasks) == 2
    assert len(repository.list_analyses(str(field.id))) == 2
