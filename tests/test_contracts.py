from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from agriculture.domain import (
    AnalysisStateMachine,
    AnalysisStatus,
    InvalidAnalysisTransition,
)
from agriculture.schemas import (
    AgentSession,
    AgentSessionChannel,
    AgentSessionStatus,
    Analysis,
    AnalysisError,
    AnalysisStage,
    BoundarySuggestion,
    Feedback,
    FeedbackRating,
    Field,
    GeoJSONPolygon,
    ReclusterRequest,
    ZoneGeoJSONMultiPolygon,
    ZoneGeoJSONPolygon,
)

FIXTURES = Path(__file__).parents[1] / "agriculture" / "fixtures"
FIELD_ID = UUID("11111111-1111-4111-8111-111111111111")
ANALYSIS_ID = UUID("33333333-3333-4333-8333-333333333333")


@pytest.mark.parametrize(
    ("filename", "contract"),
    [
        ("field-draft.example.json", Field),
        ("boundary-suggestion.example.json", BoundarySuggestion),
        ("analysis-running.example.json", Analysis),
        ("analysis-result.example.json", Analysis),
    ],
)
def test_stable_fixtures_validate_and_round_trip(
    filename: str, contract: type[Field | BoundarySuggestion | Analysis]
) -> None:
    fixture_json = (FIXTURES / filename).read_text(encoding="utf-8")

    parsed = contract.model_validate_json(fixture_json)
    reparsed = contract.model_validate_json(parsed.model_dump_json())

    assert reparsed == parsed
    assert parsed.schema_version == "1.0"


def test_result_fixture_contains_four_temporal_non_diagnostic_zones() -> None:
    analysis = Analysis.model_validate_json(
        (FIXTURES / "analysis-result.example.json").read_text(encoding="utf-8")
    )

    assert analysis.status is AnalysisStatus.COMPLETED
    assert analysis.result is not None
    assert analysis.result.selected_zone_count == 4
    assert len(analysis.result.zones) == 4
    assert all(len(zone.trajectory) == 3 for zone in analysis.result.zones)
    assert analysis.result.scope.diagnostic is False
    assert set(analysis.result.scope.excluded_inferences) == {
        "pest",
        "disease",
        "soil",
        "water",
    }
    assert set(analysis.result.provenance.indices) == {"NDVI", "NDRE", "NDMI"}


def _polygon(ring: tuple[tuple[float, float], ...]) -> GeoJSONPolygon:
    return GeoJSONPolygon.model_validate({"type": "Polygon", "coordinates": (ring,)})


def test_polygon_must_be_closed() -> None:
    with pytest.raises(ValidationError, match="must be closed"):
        _polygon(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))


def test_polygon_rejects_self_intersection() -> None:
    with pytest.raises(ValidationError, match="self-intersect"):
        _polygon(
            (
                (0.0, 0.0),
                (1.0, 1.0),
                (0.0, 1.0),
                (1.0, 0.0),
                (0.0, 0.0),
            )
        )


def test_polygon_rejects_antimeridian_crossing() -> None:
    with pytest.raises(ValidationError, match="antimeridian"):
        _polygon(
            (
                (179.0, 0.0),
                (-179.0, 0.0),
                (-179.0, 1.0),
                (179.0, 1.0),
                (179.0, 0.0),
            )
        )


def test_polygon_has_one_ring_and_at_most_200_vertices() -> None:
    ring = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0))
    with pytest.raises(ValidationError):
        GeoJSONPolygon.model_validate({"type": "Polygon", "coordinates": ()})
    with pytest.raises(ValidationError):
        GeoJSONPolygon.model_validate({"type": "Polygon", "coordinates": (ring, ring)})

    too_many = tuple((float(index) / 1000.0, float(index % 2)) for index in range(200)) + (
        (0.0, 0.0),
    )
    with pytest.raises(ValidationError, match="more than 200"):
        _polygon(too_many)


def test_zone_geometry_contracts_preserve_holes_and_disconnected_components() -> None:
    exterior = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0))
    hole = ((1.0, 1.0), (1.0, 2.0), (2.0, 2.0), (2.0, 1.0), (1.0, 1.0))
    second = ((5.0, 0.0), (6.0, 0.0), (6.0, 1.0), (5.0, 1.0), (5.0, 0.0))

    polygon = ZoneGeoJSONPolygon(coordinates=(exterior, hole))
    multipolygon = ZoneGeoJSONMultiPolygon(coordinates=((exterior, hole), (second,)))

    assert len(polygon.coordinates) == 2
    assert len(multipolygon.coordinates) == 2
    assert len(multipolygon.coordinates[0]) == 2
    with pytest.raises(ValidationError):
        GeoJSONPolygon(coordinates=(exterior, hole))


def test_zone_geometry_contracts_reject_invalid_ring_relationships() -> None:
    exterior = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0))
    outside_hole = ((5.0, 5.0), (5.0, 6.0), (6.0, 6.0), (6.0, 5.0), (5.0, 5.0))
    overlapping = ((3.0, 0.0), (5.0, 0.0), (5.0, 2.0), (3.0, 2.0), (3.0, 0.0))

    with pytest.raises(ValidationError):
        ZoneGeoJSONPolygon(coordinates=(exterior, outside_hole))
    with pytest.raises(ValidationError):
        ZoneGeoJSONMultiPolygon(coordinates=((exterior,), (overlapping,)))


def test_field_limits_area_and_season_length() -> None:
    field = Field.model_validate_json(
        (FIXTURES / "field-draft.example.json").read_text(encoding="utf-8")
    )
    invalid_area = field.model_dump()
    invalid_area["estimated_area_ha"] = 0.0
    with pytest.raises(ValidationError):
        Field.model_validate(invalid_area)

    invalid_season = field.model_dump()
    invalid_season["season_end"] = invalid_season["season_start"].replace(
        year=invalid_season["season_start"].year + 2
    )
    with pytest.raises(ValidationError, match="365"):
        Field.model_validate(invalid_season)


def test_contracts_forbid_unknown_fields_and_naive_timestamps() -> None:
    field = Field.model_validate_json(
        (FIXTURES / "field-draft.example.json").read_text(encoding="utf-8")
    )
    unknown = field.model_dump()
    unknown["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        Field.model_validate(unknown)

    naive = field.model_dump()
    naive["created_at"] = datetime(2026, 3, 28, 12, 0, 0)
    with pytest.raises(ValidationError, match="timezone"):
        Field.model_validate(naive)


def test_completed_requires_result_and_failed_requires_error() -> None:
    completed = Analysis.model_validate_json(
        (FIXTURES / "analysis-result.example.json").read_text(encoding="utf-8")
    )
    completed_without_result = completed.model_dump()
    completed_without_result["result"] = None
    with pytest.raises(ValidationError, match="requires a result"):
        Analysis.model_validate(completed_without_result)

    running = Analysis.model_validate_json(
        (FIXTURES / "analysis-running.example.json").read_text(encoding="utf-8")
    )
    failed_without_error = running.model_dump()
    failed_without_error["status"] = AnalysisStatus.FAILED
    failed_without_error["progress"]["stage"] = AnalysisStage.FAILED
    with pytest.raises(ValidationError, match="requires an error"):
        Analysis.model_validate(failed_without_error)

    failed = running.model_dump()
    failed["status"] = AnalysisStatus.FAILED
    failed["progress"]["stage"] = AnalysisStage.FAILED
    failed["error"] = AnalysisError(
        code="SCENE_UNAVAILABLE",
        message="No usable Sentinel-2 scene was available.",
        retryable=True,
        occurred_at=datetime.now(UTC),
    )
    assert Analysis.model_validate(failed).status is AnalysisStatus.FAILED


def test_recluster_zone_count_is_between_two_and_seven() -> None:
    valid = {
        "schema_version": "1.0",
        "analysis_id": ANALYSIS_ID,
        "zone_count": 4,
        "requested_at": datetime.now(UTC),
    }
    assert ReclusterRequest.model_validate(valid).zone_count == 4
    for invalid_count in (1, 8):
        invalid = {**valid, "zone_count": invalid_count}
        with pytest.raises(ValidationError):
            ReclusterRequest.model_validate(invalid)


def test_state_machine_allows_only_forward_monotonic_updates() -> None:
    assert AnalysisStateMachine.can_transition(
        AnalysisStatus.QUEUED,
        0,
        AnalysisStatus.RUNNING,
        10,
    )
    assert AnalysisStateMachine.can_transition(
        AnalysisStatus.RUNNING,
        80,
        AnalysisStatus.COMPLETED,
        100,
    )
    assert not AnalysisStateMachine.can_transition(
        AnalysisStatus.RUNNING,
        80,
        AnalysisStatus.RUNNING,
        70,
    )
    with pytest.raises(InvalidAnalysisTransition, match="cannot transition"):
        AnalysisStateMachine.validate_transition(
            AnalysisStatus.COMPLETED,
            100,
            AnalysisStatus.RUNNING,
            100,
        )


def test_analysis_cannot_be_its_own_parent() -> None:
    running = Analysis.model_validate_json(
        (FIXTURES / "analysis-running.example.json").read_text(encoding="utf-8")
    )
    data = running.model_dump()
    data["parent_analysis_id"] = data["id"]
    with pytest.raises(ValidationError, match="own parent"):
        Analysis.model_validate(data)


def test_fixture_ids_link_the_same_field() -> None:
    field = Field.model_validate_json(
        (FIXTURES / "field-draft.example.json").read_text(encoding="utf-8")
    )
    boundary = BoundarySuggestion.model_validate_json(
        (FIXTURES / "boundary-suggestion.example.json").read_text(encoding="utf-8")
    )
    analysis = Analysis.model_validate_json(
        (FIXTURES / "analysis-result.example.json").read_text(encoding="utf-8")
    )

    assert field.id == FIELD_ID == boundary.field_id == analysis.field_id


def test_agent_session_and_feedback_contracts_support_voice_zone_feedback() -> None:
    now = datetime.now(UTC)
    session_id = UUID("44444444-4444-4444-8444-444444444444")
    session = AgentSession(
        id=session_id,
        language="pt-BR",
        channel=AgentSessionChannel.VOICE,
        status=AgentSessionStatus.ACTIVE,
        field_id=FIELD_ID,
        analysis_id=ANALYSIS_ID,
        turn_count=2,
        started_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    feedback = Feedback(
        id=UUID("55555555-5555-4555-8555-555555555555"),
        analysis_id=ANALYSIS_ID,
        session_id=session.id,
        rating=FeedbackRating.UNCLEAR,
        comment="Quero comparar a zona 1 novamente.",
        zone_id="zone-1",
        created_at=now,
    )

    assert session.channel is AgentSessionChannel.VOICE
    assert feedback.rating is FeedbackRating.UNCLEAR
    assert feedback.zone_id == "zone-1"
