from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event

import pytest

from agriculture.adapters import InMemoryAgricultureRepository, InMemoryTaskQueue
from agriculture.api.errors import APIError
from agriculture.api.models import (
    AgentSessionCreateInput,
    AgentSessionPatchInput,
    AnalysisCreateInput,
    FeedbackCreateInput,
    FieldCreateInput,
    FieldPatchInput,
)
from agriculture.container import reset_container
from agriculture.domain import AnalysisStatus
from agriculture.fixture_loader import fixture_names, load_fixture
from agriculture.schemas import (
    AgentSessionChannel,
    AgentSessionStatus,
    Analysis,
    BoundarySuggestion,
    FeedbackRating,
    Field,
    GeoJSONPoint,
)
from agriculture.services.application import AgricultureService
from agriculture.services.idempotency import IdempotencyContext

FIXED_NOW = datetime(2026, 8, 29, 15, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_agriculture_container() -> Iterator[None]:
    reset_container()
    yield
    reset_container()


@pytest.fixture
def service_bundle() -> tuple[
    AgricultureService,
    InMemoryAgricultureRepository,
    InMemoryTaskQueue,
]:
    repository = InMemoryAgricultureRepository(clock=lambda: FIXED_NOW)
    queue = InMemoryTaskQueue(clock=lambda: FIXED_NOW)
    service = AgricultureService(repository, queue, clock=lambda: FIXED_NOW)
    return service, repository, queue


def _context(key: str, digest: str | None = None) -> IdempotencyContext:
    return IdempotencyContext(scoped_key=key, request_digest=digest or f"digest-{key}")


def _field_input() -> FieldCreateInput:
    return FieldCreateInput(
        name="Talhão de serviço",
        crop="soja",
        season_start=date(2025, 10, 15),
        season_end=date(2026, 3, 10),
        estimated_area_ha=12.4,
        reference_location=GeoJSONPoint(coordinates=(-48.9029, -23.9786)),
    )


def _create_field(
    service: AgricultureService,
    repository: InMemoryAgricultureRepository,
    *,
    key: str = "field-service-001",
) -> Field:
    result = service.create_field(_field_input(), _context(key))
    field = repository.get_field(result.data["id"])
    assert field is not None
    return field


def _confirm_field(
    service: AgricultureService,
    repository: InMemoryAgricultureRepository,
    field: Field,
    *,
    key: str = "boundary-service-001",
) -> Field:
    suggestion_result = service.suggest_boundary(field.id, _context(key))
    suggestion = BoundarySuggestion.model_validate_json(json.dumps(suggestion_result.data))
    updated = service.patch_field(
        field.id,
        FieldPatchInput(
            boundary=suggestion.boundary,
            boundary_confirmed=True,
        ),
    )
    stored = repository.get_field(str(updated.id))
    assert stored is not None
    return stored


def test_field_service_persists_and_maps_invalid_patch_to_api_error(
    service_bundle: tuple[
        AgricultureService,
        InMemoryAgricultureRepository,
        InMemoryTaskQueue,
    ],
) -> None:
    service, repository, _queue = service_bundle
    field = _create_field(service, repository)

    assert field.created_at == FIXED_NOW
    assert service.get_field(field.id) == field
    assert service.list_fields() == [field]

    with pytest.raises(APIError) as captured:
        service.patch_field(
            field.id,
            FieldPatchInput(season_end=date(2027, 3, 10)),
        )
    assert captured.value.status == 422
    assert captured.value.code == "validation_error"

    with pytest.raises(APIError) as explicit_null:
        service.patch_field(field.id, FieldPatchInput(name=None))
    assert explicit_null.value.status == 422
    assert explicit_null.value.code == "validation_error"


def test_concurrent_identical_field_posts_create_once_and_replay_response() -> None:
    class CoordinatedRepository(InMemoryAgricultureRepository):
        def __init__(self) -> None:
            super().__init__(clock=lambda: FIXED_NOW)
            self.owner_saving = Event()
            self.contender_claimed = Event()
            self.release_owner = Event()

        def get_idempotency(self, *args, **kwargs):
            record = super().get_idempotency(*args, **kwargs)
            if record is not None and record.pending:
                self.contender_claimed.set()
            return record

        def save_field(self, field: Field) -> Field:
            self.owner_saving.set()
            assert self.release_owner.wait(timeout=2)
            return super().save_field(field)

    repository = CoordinatedRepository()
    service = AgricultureService(
        repository,
        InMemoryTaskQueue(clock=lambda: FIXED_NOW),
        clock=lambda: FIXED_NOW,
    )
    context = _context("concurrent-field")

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(service.create_field, _field_input(), context)
        assert repository.owner_saving.wait(timeout=2)
        contender = executor.submit(service.create_field, _field_input(), context)
        assert repository.contender_claimed.wait(timeout=2)
        repository.release_owner.set()
        results = [owner.result(timeout=2), contender.result(timeout=2)]

    assert sum(result.replayed for result in results) == 1
    assert results[0].data == results[1].data
    assert len(repository.list_fields()) == 1


def test_boundary_suggestion_is_shifted_to_reference_location_and_idempotent(
    service_bundle: tuple[
        AgricultureService,
        InMemoryAgricultureRepository,
        InMemoryTaskQueue,
    ],
) -> None:
    service, repository, _queue = service_bundle
    field = _create_field(service, repository)
    context = _context("boundary-idempotent")

    first = service.suggest_boundary(field.id, context)
    replay = service.suggest_boundary(field.id, context)
    suggestion = BoundarySuggestion.model_validate_json(json.dumps(first.data))
    unique_vertices = suggestion.boundary.coordinates[0][:-1]
    centroid = (
        sum(point[0] for point in unique_vertices) / len(unique_vertices),
        sum(point[1] for point in unique_vertices) / len(unique_vertices),
    )

    assert first.status == replay.status == 201
    assert replay.replayed is True
    assert replay.data == first.data
    assert centroid == pytest.approx(field.reference_location.coordinates)

    with pytest.raises(APIError) as captured:
        service.suggest_boundary(field.id, _context(context.scoped_key, "changed-digest"))
    assert captured.value.code == "idempotency_key_conflict"
    assert captured.value.status == 409


def test_analysis_requires_confirmation_enqueues_and_enforces_daily_quota(
    service_bundle: tuple[
        AgricultureService,
        InMemoryAgricultureRepository,
        InMemoryTaskQueue,
    ],
) -> None:
    service, repository, queue = service_bundle
    draft = _create_field(service, repository)
    payload = AnalysisCreateInput(field_id=draft.id, requested_zone_count=4)

    with pytest.raises(APIError) as unconfirmed:
        service.create_analysis(
            payload,
            _context("analysis-unconfirmed"),
            actor_id="farmer-1",
        )
    assert unconfirmed.value.code == "field_boundary_not_confirmed"

    field = _confirm_field(service, repository, draft)
    payload = AnalysisCreateInput(field_id=field.id, requested_zone_count=4)
    created = []
    for number in range(1, 4):
        context = _context(f"analysis-{number}")
        result = service.create_analysis(payload, context, actor_id="farmer-1")
        created.append((result, context))
        assert result.status == 202
        assert result.data["status"] == "queued"

    replay = service.create_analysis(payload, created[0][1], actor_id="farmer-1")
    assert replay.replayed is True
    assert replay.data == created[0][0].data
    assert len(queue.tasks) == 3
    assert all(task.name == "internal/tasks/analyses" for task in queue.tasks)

    with pytest.raises(APIError) as limited:
        service.create_analysis(
            payload,
            _context("analysis-4"),
            actor_id="farmer-1",
        )
    assert limited.value.status == 429
    assert limited.value.code == "daily_analysis_limit_exceeded"
    assert int(limited.value.headers["Retry-After"]) > 0


def test_completed_idempotent_replay_does_not_depend_on_later_domain_state(
    service_bundle: tuple[
        AgricultureService,
        InMemoryAgricultureRepository,
        InMemoryTaskQueue,
    ],
) -> None:
    service, repository, _queue = service_bundle
    field = _confirm_field(service, repository, _create_field(service, repository))
    payload = AnalysisCreateInput(field_id=field.id, requested_zone_count=4)
    context = _context("stable-analysis-replay")

    first = service.create_analysis(payload, context, actor_id="farmer-stable-replay")
    service.patch_field(
        field.id,
        FieldPatchInput(boundary_confirmed=False),
    )
    replay = service.create_analysis(payload, context, actor_id="farmer-stable-replay")

    assert replay.replayed is True
    assert replay.data == first.data


def test_agent_session_feedback_and_cross_analysis_protection(
    service_bundle: tuple[
        AgricultureService,
        InMemoryAgricultureRepository,
        InMemoryTaskQueue,
    ],
) -> None:
    service, repository, _queue = service_bundle
    field = _confirm_field(service, repository, _create_field(service, repository))
    analysis_one_result = service.create_analysis(
        AnalysisCreateInput(field_id=field.id, requested_zone_count=4),
        _context("session-analysis-1"),
        actor_id="farmer-session",
    )
    analysis_two_result = service.create_analysis(
        AnalysisCreateInput(field_id=field.id, requested_zone_count=5),
        _context("session-analysis-2"),
        actor_id="farmer-session",
    )
    analysis_one = Analysis.model_validate_json(json.dumps(analysis_one_result.data))
    analysis_two = Analysis.model_validate_json(json.dumps(analysis_two_result.data))
    other_field = _confirm_field(
        service,
        repository,
        _create_field(service, repository, key="field-session-other"),
        key="boundary-session-other",
    )
    analysis_two = analysis_two.model_copy(update={"field_id": other_field.id})
    repository.save_analysis(analysis_two)

    with pytest.raises(APIError) as create_context_mismatch:
        service.create_agent_session(
            AgentSessionCreateInput(
                field_id=field.id,
                analysis_id=analysis_two.id,
            ),
            _context("session-create-mismatch"),
        )
    assert create_context_mismatch.value.status == 409
    assert create_context_mismatch.value.code == "agent_session_context_mismatch"

    session_result = service.create_agent_session(
        AgentSessionCreateInput(
            language="pt-BR",
            channel=AgentSessionChannel.VOICE,
            field_id=field.id,
            analysis_id=analysis_one.id,
        ),
        _context("session-create"),
    )
    session_replay = service.create_agent_session(
        AgentSessionCreateInput(
            language="pt-BR",
            channel=AgentSessionChannel.VOICE,
            field_id=field.id,
            analysis_id=analysis_one.id,
        ),
        _context("session-create"),
    )
    assert session_replay.replayed is True
    assert session_replay.data == session_result.data
    session = service.get_agent_session(session_result.data["id"])
    assert session.status is AgentSessionStatus.ACTIVE
    assert session.expires_at == FIXED_NOW + timedelta(hours=1)

    patched = service.patch_agent_session(
        session.id,
        AgentSessionPatchInput(increment_turn_count=True),
    )
    assert patched.turn_count == 1

    with pytest.raises(APIError) as patch_context_mismatch:
        service.patch_agent_session(
            session.id,
            AgentSessionPatchInput(field_id=other_field.id),
        )
    assert patch_context_mismatch.value.status == 409
    assert patch_context_mismatch.value.code == "agent_session_context_mismatch"

    with pytest.raises(APIError) as explicit_null:
        service.patch_agent_session(
            session.id,
            AgentSessionPatchInput(status=None),
        )
    assert explicit_null.value.code == "validation_error"

    with pytest.raises(APIError) as unavailable_zone:
        service.create_feedback(
            FeedbackCreateInput(
                analysis_id=analysis_one.id,
                session_id=session.id,
                rating=FeedbackRating.UNCLEAR,
                zone_id="zone-1",
            ),
            _context("feedback-before-result"),
        )
    assert unavailable_zone.value.status == 409
    assert unavailable_zone.value.code == "analysis_result_unavailable"

    feedback = service.create_feedback(
        FeedbackCreateInput(
            analysis_id=analysis_one.id,
            session_id=session.id,
            rating=FeedbackRating.HELPFUL,
            comment="A comparação ficou clara.",
        ),
        _context("feedback-create"),
    )
    feedback_replay = service.create_feedback(
        FeedbackCreateInput(
            analysis_id=analysis_one.id,
            session_id=session.id,
            rating=FeedbackRating.HELPFUL,
            comment="A comparação ficou clara.",
        ),
        _context("feedback-create"),
    )
    assert feedback.status == 201
    assert feedback.data["rating"] == "helpful"
    assert feedback_replay.replayed is True
    assert feedback_replay.data == feedback.data
    assert len(repository.list_feedback(str(analysis_one.id))) == 1

    with pytest.raises(APIError) as mismatch:
        service.create_feedback(
            FeedbackCreateInput(
                analysis_id=analysis_two.id,
                session_id=session.id,
                rating=FeedbackRating.UNCLEAR,
            ),
            _context("feedback-mismatch"),
        )
    assert mismatch.value.status == 409
    assert mismatch.value.code == "feedback_session_mismatch"


def test_completed_analysis_can_be_reclustered_with_parent_link(
    service_bundle: tuple[
        AgricultureService,
        InMemoryAgricultureRepository,
        InMemoryTaskQueue,
    ],
) -> None:
    service, repository, queue = service_bundle
    field = _confirm_field(service, repository, _create_field(service, repository))
    source_fixture = load_fixture("analysis-result")
    assert isinstance(source_fixture, Analysis)
    source = source_fixture.model_copy(update={"field_id": field.id})
    repository.save_analysis(source)

    result = service.recluster_analysis(
        source.id,
        6,
        _context("recluster-analysis-001"),
        actor_id="farmer-recluster",
    )

    assert result.status == 202
    assert result.data["status"] == "queued"
    assert result.data["parent_analysis_id"] == str(source.id)
    assert result.data["requested_zone_count"] == 6
    assert queue.tasks[-1].payload["parent_analysis_id"] == str(source.id)

    replay = service.recluster_analysis(
        source.id,
        6,
        _context("recluster-analysis-001"),
        actor_id="farmer-recluster",
    )
    assert replay.replayed is True
    assert replay.data == result.data
    assert len(queue.tasks) == 1


def test_fixture_loader_returns_validated_contracts() -> None:
    assert fixture_names() == (
        "field-draft",
        "boundary-suggestion",
        "analysis-running",
        "analysis-result",
    )
    assert isinstance(load_fixture("field-draft"), Field)
    assert isinstance(load_fixture("boundary-suggestion"), BoundarySuggestion)
    running = load_fixture("analysis-running")
    result = load_fixture("analysis-result")
    assert isinstance(running, Analysis)
    assert isinstance(result, Analysis)
    assert running.status is AnalysisStatus.RUNNING
    assert result.status is AnalysisStatus.COMPLETED

    with pytest.raises(KeyError, match="Unknown fixture"):
        load_fixture("unknown")
