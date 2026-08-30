"""Prepare a reproducible, coordinate-redacted real Sentinel demonstration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Protocol, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import Field as PydanticField

from agriculture.domain import AnalysisStatus
from agriculture.ports.repositories import AgricultureRepository
from agriculture.schemas import (
    Analysis,
    AnalysisMode,
    AnalysisProgress,
    AnalysisStage,
    Field,
    GeoJSONPoint,
    GeoJSONPolygon,
)

_FIELD_NAMESPACE = "agentic-agriculture:authorized-real-demo:field:"
_ANALYSIS_NAMESPACE = "agentic-agriculture:authorized-real-demo:analysis:"


class RealDemoPreparationError(RuntimeError):
    """A safe, operator-facing failure while preparing the real demo."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AnalysisRunner(Protocol):
    """Small portion of the Sentinel pipeline used by the preparation service."""

    def run(self, analysis_id: str) -> Any: ...


class RealDemoSpec(BaseModel):
    """Strict private input; instances must never be serialized into the manifest."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    schema_version: Literal["1.0"] = "1.0"
    demo_key: Annotated[
        str,
        PydanticField(pattern=r"^[a-z0-9][a-z0-9_-]{7,63}$"),
    ]
    name: Annotated[str, PydanticField(min_length=1, max_length=120)]
    crop: Annotated[str, PydanticField(min_length=1, max_length=80)]
    season_start: date
    season_end: date
    estimated_area_ha: Annotated[float, PydanticField(gt=0.0, le=500.0)]
    reference_location: GeoJSONPoint
    boundary: GeoJSONPolygon
    requested_zone_count: Annotated[int, PydanticField(ge=2, le=7)] = 4

    @model_validator(mode="after")
    def validate_private_field(self) -> Self:
        season_days = (self.season_end - self.season_start).days
        if not 1 <= season_days <= 365:
            raise ValueError("The crop season must contain between 1 and 365 days.")
        point = self.reference_location.coordinates
        rings = self.boundary.coordinates
        if not _point_in_ring(point, rings[0]) or any(
            _point_in_ring(point, hole) for hole in rings[1:]
        ):
            raise ValueError("The reference location must be inside the supplied field boundary.")
        return self


@dataclass(frozen=True, slots=True)
class PreparedRealDemo:
    field: Field
    analysis: Analysis
    field_created: bool
    cached_result_reused: bool


def _point_in_ring(
    point: tuple[float, float],
    ring: tuple[tuple[float, float], ...],
) -> bool:
    """Return true for points inside or on the edge of a simple closed ring."""

    x, y = point
    inside = False
    for start, end in zip(ring, ring[1:], strict=False):
        x1, y1 = start
        x2, y2 = end
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if (
            abs(cross) <= 1e-12
            and min(x1, x2) - 1e-12 <= x <= max(x1, x2) + 1e-12
            and (min(y1, y2) - 1e-12 <= y <= max(y1, y2) + 1e-12)
        ):
            return True
        if (y1 > y) != (y2 > y):
            intersection_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < intersection_x:
                inside = not inside
    return inside


def _field_id(demo_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{_FIELD_NAMESPACE}{demo_key}")


def _analysis_id(field_id: UUID, requested_zone_count: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"{_ANALYSIS_NAMESPACE}{field_id}:{requested_zone_count}",
    )


def _field_payload(field: Field) -> dict[str, Any]:
    return field.model_dump(
        mode="json",
        exclude={"id", "created_at", "updated_at"},
    )


def _expected_field_payload(spec: RealDemoSpec) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "name": spec.name,
        "crop": spec.crop,
        "season_start": spec.season_start.isoformat(),
        "season_end": spec.season_end.isoformat(),
        "estimated_area_ha": spec.estimated_area_ha,
        "reference_location": spec.reference_location.model_dump(mode="json"),
        "boundary": spec.boundary.model_dump(mode="json"),
        "boundary_confirmed": True,
    }


def _create_field(spec: RealDemoSpec, *, now: datetime) -> Field:
    return Field(
        id=_field_id(spec.demo_key),
        name=spec.name,
        crop=spec.crop,
        season_start=spec.season_start,
        season_end=spec.season_end,
        estimated_area_ha=spec.estimated_area_ha,
        reference_location=spec.reference_location,
        boundary=spec.boundary,
        boundary_confirmed=True,
        created_at=now,
        updated_at=now,
    )


def _create_analysis(spec: RealDemoSpec, field: Field, *, now: datetime) -> Analysis:
    return Analysis(
        id=_analysis_id(field.id, spec.requested_zone_count),
        field_id=field.id,
        parent_analysis_id=None,
        status=AnalysisStatus.QUEUED,
        requested_zone_count=spec.requested_zone_count,
        progress=AnalysisProgress(
            percent=0,
            stage=AnalysisStage.QUEUED,
            message_pt="Demonstração real adicionada à fila de preparação.",
            message_en="Real demonstration added to the preparation queue.",
            updated_at=now,
        ),
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )


def _require_real_completed_result(analysis: Analysis) -> None:
    result = analysis.result
    if analysis.status is not AnalysisStatus.COMPLETED or result is None:
        raise RealDemoPreparationError(
            "INVALID_CACHED_ANALYSIS",
            "A completed real demonstration result is required.",
        )
    fixture_markers = ("DEMO", "FIXTURE", "SAMPLE")
    if result.mode is not AnalysisMode.LIVE or any(
        marker in scene.scene_id.upper() for marker in fixture_markers for scene in result.scenes
    ):
        raise RealDemoPreparationError(
            "RESULT_NOT_FROM_LIVE_PIPELINE",
            "The analysis is a fixture or sample, not a live Sentinel pipeline result.",
        )


def prepare_real_demo(
    spec: RealDemoSpec,
    repository: AgricultureRepository,
    pipeline: AnalysisRunner | None,
    *,
    reuse_only: bool = False,
    clock=lambda: datetime.now(UTC),
) -> PreparedRealDemo:
    """Persist the authorized field and synchronously prepare or reuse its result."""

    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")

    deterministic_field_id = _field_id(spec.demo_key)
    field = repository.get_field(str(deterministic_field_id))
    field_created = field is None
    if field is None:
        if reuse_only:
            raise RealDemoPreparationError(
                "REAL_DEMO_CACHE_MISS",
                "No completed real demonstration is cached for this demo_key and zone count.",
            )
        field = repository.save_field(_create_field(spec, now=now))
    elif _field_payload(field) != _expected_field_payload(spec):
        raise RealDemoPreparationError(
            "DEMO_KEY_CONFLICT",
            "The demo_key already identifies a different private field input. "
            "Choose a new non-sensitive demo_key; existing coordinates were not displayed.",
        )

    deterministic_analysis_id = _analysis_id(field.id, spec.requested_zone_count)
    analysis = repository.get_analysis(str(deterministic_analysis_id))
    if analysis is not None and (
        analysis.field_id != field.id or analysis.requested_zone_count != spec.requested_zone_count
    ):
        raise RealDemoPreparationError(
            "INVALID_CACHED_ANALYSIS",
            "The deterministic analysis identity is linked to incompatible cached metadata.",
        )
    if analysis is not None and analysis.status is AnalysisStatus.COMPLETED:
        _require_real_completed_result(analysis)
        return PreparedRealDemo(
            field=field,
            analysis=analysis,
            field_created=field_created,
            cached_result_reused=True,
        )

    if reuse_only:
        raise RealDemoPreparationError(
            "REAL_DEMO_CACHE_MISS",
            "No completed real demonstration is cached for this demo_key and zone count.",
        )
    if pipeline is None:
        raise RealDemoPreparationError(
            "SENTINEL_PIPELINE_DISABLED",
            "The Sentinel analysis pipeline must be enabled to prepare a new real demo.",
        )
    if analysis is None:
        analysis = repository.save_analysis(_create_analysis(spec, field, now=now))
    elif analysis.status is AnalysisStatus.FAILED and not (
        analysis.error and analysis.error.retryable
    ):
        raise RealDemoPreparationError(
            "CACHED_ANALYSIS_NOT_RETRYABLE",
            "The deterministic analysis previously failed permanently. Review it and use a "
            "new demo_key only after the private input or processing plan changes.",
        )

    try:
        outcome = pipeline.run(str(analysis.id))
    except Exception as exc:  # noqa: BLE001 - private exception details must not escape
        raise RealDemoPreparationError(
            "REAL_DEMO_PIPELINE_ERROR",
            f"The Sentinel pipeline raised {type(exc).__name__}; private details were suppressed.",
        ) from None
    completed = repository.get_analysis(str(analysis.id))
    if completed is None or completed.status is not AnalysisStatus.COMPLETED:
        status = str(getattr(outcome, "status", "unknown"))
        error_code = getattr(outcome, "error_code", None)
        suffix = f" ({error_code})" if error_code else ""
        raise RealDemoPreparationError(
            "REAL_DEMO_ANALYSIS_INCOMPLETE",
            f"The real Sentinel analysis did not complete: {status}{suffix}.",
        )
    _require_real_completed_result(completed)
    return PreparedRealDemo(
        field=field,
        analysis=completed,
        field_created=field_created,
        cached_result_reused=False,
    )


def build_redacted_demo_manifest(
    prepared: PreparedRealDemo,
    *,
    authorization_asserted: bool,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Project a completed analysis without any field or zone coordinates."""

    if not authorization_asserted:
        raise ValueError("authorization_asserted must be true")
    analysis = prepared.analysis
    _require_real_completed_result(analysis)
    result = analysis.result
    assert result is not None
    instant = generated_at or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")

    return {
        "schema_version": "1.0",
        "manifest_type": "authorized_real_sentinel_demo",
        "generated_at": instant.astimezone(UTC).isoformat(),
        "authorization": {
            "operator_asserted_authorized_use": True,
            "private_authorization_details_omitted": True,
        },
        "redaction": {
            "coordinates_omitted": True,
            "field_boundary_omitted": True,
            "zone_boundaries_omitted": True,
            "private_input_path_omitted": True,
        },
        "execution": {
            "field_created": prepared.field_created,
            "cached_result_reused": prepared.cached_result_reused,
        },
        "field": {
            "field_id": str(prepared.field.id),
            "crop": prepared.field.crop,
            "season_start": prepared.field.season_start.isoformat(),
            "season_end": prepared.field.season_end.isoformat(),
            "estimated_area_ha": prepared.field.estimated_area_ha,
            "boundary_confirmed": prepared.field.boundary_confirmed,
        },
        "analysis": {
            "analysis_id": str(analysis.id),
            "status": analysis.status.value,
            "mode": result.mode.value,
            "generated_at": result.generated_at.isoformat(),
            "selected_zone_count": result.selected_zone_count,
            "scenes": [
                {
                    "scene_id": scene.scene_id,
                    "captured_at": scene.captured_at.isoformat(),
                    "cloud_cover_percent": scene.cloud_cover_percent,
                    "field_indices": scene.field_indices.model_dump(mode="json"),
                }
                for scene in result.scenes
            ],
            "zones": [
                {
                    "zone_id": zone.zone_id,
                    "relative_label": zone.relative_label.value,
                    "area_ha": zone.area_ha,
                    "area_percent": zone.area_percent,
                    "summary_pt": zone.summary_pt,
                    "summary_en": zone.summary_en,
                    "trajectory": [point.model_dump(mode="json") for point in zone.trajectory],
                }
                for zone in result.zones
            ],
            "scope": result.scope.model_dump(mode="json"),
            "provenance": result.provenance.model_dump(mode="json"),
            "artifacts": result.artifacts.model_dump(mode="json"),
        },
    }


__all__ = [
    "PreparedRealDemo",
    "RealDemoPreparationError",
    "RealDemoSpec",
    "build_redacted_demo_manifest",
    "prepare_real_demo",
]
