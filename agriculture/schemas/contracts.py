"""Versioned, strict wire contracts for the MVP API and UI fixtures."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from math import isclose
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)
from pydantic import Field as PydanticField

from agriculture.domain import AnalysisStatus

type ContractVersion = Literal["1.0"]
type Longitude = Annotated[float, PydanticField(ge=-180.0, le=180.0)]
type Latitude = Annotated[float, PydanticField(ge=-90.0, le=90.0)]
type Position = tuple[Longitude, Latitude]
type AreaHectares = Annotated[float, PydanticField(gt=0.0, le=500.0)]
type ZoneCount = Annotated[int, PydanticField(ge=2, le=7)]
type IndexValue = Annotated[float, PydanticField(ge=-1.0, le=1.0)]


class StrictContract(BaseModel):
    """Base configuration shared by all external contracts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
    )


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset.")
    return value


def _orientation(a: Position, b: Position, c: Position) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Position, b: Position, point: Position) -> bool:
    epsilon = 1e-12
    return (
        min(a[0], b[0]) - epsilon <= point[0] <= max(a[0], b[0]) + epsilon
        and min(a[1], b[1]) - epsilon <= point[1] <= max(a[1], b[1]) + epsilon
    )


def _segments_intersect(a: Position, b: Position, c: Position, d: Position) -> bool:
    epsilon = 1e-12
    orientations = (
        _orientation(a, b, c),
        _orientation(a, b, d),
        _orientation(c, d, a),
        _orientation(c, d, b),
    )
    o1, o2, o3, o4 = orientations
    if ((o1 > epsilon and o2 < -epsilon) or (o1 < -epsilon and o2 > epsilon)) and (
        (o3 > epsilon and o4 < -epsilon) or (o3 < -epsilon and o4 > epsilon)
    ):
        return True
    return (
        (isclose(o1, 0.0, abs_tol=epsilon) and _on_segment(a, b, c))
        or (isclose(o2, 0.0, abs_tol=epsilon) and _on_segment(a, b, d))
        or (isclose(o3, 0.0, abs_tol=epsilon) and _on_segment(c, d, a))
        or (isclose(o4, 0.0, abs_tol=epsilon) and _on_segment(c, d, b))
    )


def _validate_simple_ring(ring: tuple[Position, ...]) -> None:
    if len(ring) < 4:
        raise ValueError("A polygon ring must contain at least four positions.")
    if ring[0] != ring[-1]:
        raise ValueError("A polygon ring must be closed (first and last positions must match).")
    if any(ring[index] == ring[index + 1] for index in range(len(ring) - 1)):
        raise ValueError("A polygon ring cannot contain zero-length edges.")
    if len(set(ring[:-1])) != len(ring) - 1:
        raise ValueError("A polygon ring cannot repeat a vertex except for closure.")

    edge_count = len(ring) - 1
    for first in range(edge_count):
        for second in range(first + 1, edge_count):
            adjacent = second == first + 1 or (first == 0 and second == edge_count - 1)
            if adjacent:
                continue
            if _segments_intersect(
                ring[first],
                ring[first + 1],
                ring[second],
                ring[second + 1],
            ):
                raise ValueError("A polygon ring cannot self-intersect.")

    twice_area = sum(
        ring[index][0] * ring[index + 1][1] - ring[index + 1][0] * ring[index][1]
        for index in range(edge_count)
    )
    if isclose(twice_area, 0.0, abs_tol=1e-12):
        raise ValueError("A polygon ring must enclose a non-zero area.")


class GeoJSONPolygon(StrictContract):
    """Simple field polygon in WGS84 longitude/latitude order.

    The MVP intentionally supports a single exterior ring without holes. This
    keeps voice-assisted editing deterministic while still covering crop fields.
    """

    type: Literal["Polygon"] = "Polygon"
    coordinates: Annotated[
        tuple[tuple[Position, ...], ...], PydanticField(min_length=1, max_length=1)
    ]

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(
        cls, coordinates: tuple[tuple[Position, ...]]
    ) -> tuple[tuple[Position, ...]]:
        ring = coordinates[0]
        if len(ring) > 200:
            raise ValueError("A field polygon cannot contain more than 200 vertices.")
        _validate_simple_ring(ring)

        longitudes = [position[0] for position in ring]
        if max(longitudes) - min(longitudes) > 180.0:
            raise ValueError("Field polygons cannot cross the antimeridian.")
        if any(abs(ring[index + 1][0] - ring[index][0]) > 180.0 for index in range(len(ring) - 1)):
            raise ValueError("Field polygons cannot cross the antimeridian.")
        return coordinates


class GeoJSONPoint(StrictContract):
    """WGS84 point in GeoJSON longitude/latitude order."""

    type: Literal["Point"] = "Point"
    coordinates: Position


class Field(StrictContract):
    """A farmer-owned crop field and season used as an analysis input."""

    schema_version: ContractVersion = "1.0"
    id: UUID
    name: Annotated[str, PydanticField(min_length=1, max_length=120)]
    crop: Annotated[str, PydanticField(min_length=1, max_length=80)]
    season_start: date
    season_end: date
    estimated_area_ha: AreaHectares
    reference_location: GeoJSONPoint
    boundary: GeoJSONPolygon | None = None
    boundary_confirmed: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, getattr(info, "field_name", "timestamp"))

    @model_validator(mode="after")
    def validate_field(self) -> Self:
        season_days = (self.season_end - self.season_start).days
        if season_days <= 0:
            raise ValueError("season_end must be after season_start.")
        if season_days > 365:
            raise ValueError("A crop season cannot exceed 365 days.")
        if self.boundary_confirmed and self.boundary is None:
            raise ValueError("A confirmed field must include its boundary polygon.")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at.")
        return self


class BoundarySource(StrEnum):
    SENTINEL_2 = "sentinel-2"
    USER_DRAWN = "user-drawn"
    HYBRID = "hybrid"


class BoundarySuggestion(StrictContract):
    """A machine-generated boundary that must be confirmed by the farmer."""

    schema_version: ContractVersion = "1.0"
    id: UUID
    field_id: UUID
    boundary: GeoJSONPolygon
    estimated_area_ha: AreaHectares
    confidence: Annotated[float, PydanticField(ge=0.0, le=1.0)]
    source: BoundarySource
    requires_confirmation: Literal[True] = True
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "generated_at")


class AnalysisMode(StrEnum):
    LIVE = "live"
    CACHE = "cache"


class AnalysisStage(StrEnum):
    QUEUED = "queued"
    ACQUIRING_SCENES = "acquiring_scenes"
    COMPUTING_INDICES = "computing_indices"
    CLUSTERING_ZONES = "clustering_zones"
    GENERATING_EXPLANATION = "generating_explanation"
    COMPLETED = "completed"
    FAILED = "failed"


class VegetationIndices(StrictContract):
    """Normalized spectral indices; values always lie in [-1, 1]."""

    ndvi: IndexValue
    ndre: IndexValue
    ndmi: IndexValue


class SentinelScene(StrictContract):
    """One Sentinel-2 L2A observation used by the analysis."""

    scene_id: Annotated[str, PydanticField(min_length=1, max_length=160)]
    captured_at: datetime
    cloud_cover_percent: Annotated[float, PydanticField(ge=0.0, le=100.0)]
    field_indices: VegetationIndices
    preview_uri: Annotated[str, PydanticField(min_length=1, max_length=500)]

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "captured_at")


class TrajectoryPoint(StrictContract):
    """Spectral trajectory of a zone for one source scene."""

    scene_id: Annotated[str, PydanticField(min_length=1, max_length=160)]
    captured_at: datetime
    indices: VegetationIndices

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "captured_at")


class RelativeDevelopmentLabel(StrEnum):
    LOWER_THAN_FIELD = "lower_than_field"
    SIMILAR_TO_FIELD = "similar_to_field"
    HIGHER_THAN_FIELD = "higher_than_field"


class Zone(StrictContract):
    """One relative development zone, deliberately without a causal diagnosis."""

    zone_id: Annotated[str, PydanticField(pattern=r"^zone-[1-7]$")]
    relative_label: RelativeDevelopmentLabel
    area_ha: AreaHectares
    area_percent: Annotated[float, PydanticField(gt=0.0, le=100.0)]
    boundary: GeoJSONPolygon
    trajectory: Annotated[tuple[TrajectoryPoint, ...], PydanticField(min_length=2)]
    summary_pt: Annotated[str, PydanticField(min_length=1, max_length=500)]
    summary_en: Annotated[str, PydanticField(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def validate_trajectory(self) -> Self:
        scene_ids = [point.scene_id for point in self.trajectory]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("A zone trajectory cannot repeat a scene.")
        captured_at = [point.captured_at for point in self.trajectory]
        if captured_at != sorted(captured_at):
            raise ValueError("Zone trajectory points must be ordered chronologically.")
        return self


class AnalysisScope(StrictContract):
    """Explicit product boundary: compare spatial development, do not diagnose causes."""

    kind: Literal["relative_spatial_variability_only"] = "relative_spatial_variability_only"
    diagnostic: Literal[False] = False
    excluded_inferences: tuple[
        Literal["pest"], Literal["disease"], Literal["soil"], Literal["water"]
    ] = ("pest", "disease", "soil", "water")
    disclaimer_pt: Annotated[str, PydanticField(min_length=1, max_length=500)]
    disclaimer_en: Annotated[str, PydanticField(min_length=1, max_length=500)]


class AnalysisProvenance(StrictContract):
    """Auditable origin of every result shown in the demo."""

    provider: Literal[
        "Copernicus Data Space Ecosystem",
        "EU/ESA/Copernicus via Earth Search/AWS Open Data",
    ]
    mission: Literal["Sentinel-2"]
    product_level: Literal["L2A"]
    bands: tuple[Annotated[str, PydanticField(min_length=2, max_length=8)], ...]
    indices: tuple[Literal["NDVI", "NDRE", "NDMI"], ...]
    scene_ids: Annotated[tuple[str, ...], PydanticField(min_length=2)]
    processing_version: Annotated[str, PydanticField(min_length=1, max_length=40)]

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        if not {"B04", "B05", "B08", "B11"}.issubset(self.bands):
            raise ValueError("Provenance must include B04, B05, B08 and B11.")
        if set(self.indices) != {"NDVI", "NDRE", "NDMI"}:
            raise ValueError("Provenance must include NDVI, NDRE and NDMI exactly once.")
        if len(self.indices) != 3:
            raise ValueError("Provenance indices cannot contain duplicates.")
        if len(self.scene_ids) != len(set(self.scene_ids)):
            raise ValueError("Provenance scene_ids cannot contain duplicates.")
        return self


class ResultArtifacts(StrictContract):
    map_preview_uri: Annotated[str, PydanticField(min_length=1, max_length=500)]
    zone_geojson_uri: Annotated[str, PydanticField(min_length=1, max_length=500)]
    report_uri: Annotated[str, PydanticField(min_length=1, max_length=500)]


class AnalysisResult(StrictContract):
    """Final temporal clustering result for a field."""

    selected_zone_count: ZoneCount
    mode: AnalysisMode
    generated_at: datetime
    scenes: Annotated[tuple[SentinelScene, ...], PydanticField(min_length=2)]
    zones: Annotated[tuple[Zone, ...], PydanticField(min_length=2, max_length=7)]
    scope: AnalysisScope
    provenance: AnalysisProvenance
    artifacts: ResultArtifacts

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "generated_at")

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if len(self.zones) != self.selected_zone_count:
            raise ValueError("selected_zone_count must match the number of zones.")
        zone_ids = [zone.zone_id for zone in self.zones]
        if len(zone_ids) != len(set(zone_ids)):
            raise ValueError("Result zone IDs must be unique.")
        if not isclose(sum(zone.area_percent for zone in self.zones), 100.0, abs_tol=0.1):
            raise ValueError("Zone area percentages must total 100% (within 0.1 point).")

        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("Result scene IDs must be unique.")
        captured_at = [scene.captured_at for scene in self.scenes]
        if captured_at != sorted(captured_at):
            raise ValueError("Scenes must be ordered chronologically.")
        if tuple(scene_ids) != self.provenance.scene_ids:
            raise ValueError("Provenance scene IDs must match result scenes in order.")
        expected_scene_ids = set(scene_ids)
        for zone in self.zones:
            if {point.scene_id for point in zone.trajectory} != expected_scene_ids:
                raise ValueError(
                    "Every zone trajectory must reference every result scene exactly once."
                )
        return self


class AnalysisProgress(StrictContract):
    percent: Annotated[int, PydanticField(ge=0, le=100)]
    stage: AnalysisStage
    message_pt: Annotated[str, PydanticField(min_length=1, max_length=300)]
    message_en: Annotated[str, PydanticField(min_length=1, max_length=300)]
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def validate_updated_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "updated_at")


class AnalysisError(StrictContract):
    code: Annotated[str, PydanticField(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    message: Annotated[str, PydanticField(min_length=1, max_length=500)]
    retryable: bool
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "occurred_at")


class Analysis(StrictContract):
    """Analysis aggregate returned by create and status endpoints."""

    schema_version: ContractVersion = "1.0"
    id: UUID
    field_id: UUID
    parent_analysis_id: UUID | None = None
    status: AnalysisStatus
    requested_zone_count: ZoneCount | None = None
    progress: AnalysisProgress
    result: AnalysisResult | None = None
    error: AnalysisError | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, getattr(info, "field_name", "timestamp"))

    @model_validator(mode="after")
    def validate_state_payload(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at.")
        if self.parent_analysis_id == self.id:
            raise ValueError("An analysis cannot be its own parent.")
        if self.status is AnalysisStatus.QUEUED:
            if self.progress.percent != 0 or self.progress.stage is not AnalysisStage.QUEUED:
                raise ValueError("A queued analysis must be at the queued stage with 0% progress.")
        elif self.status is AnalysisStatus.RUNNING:
            invalid_stages = {AnalysisStage.QUEUED, AnalysisStage.COMPLETED, AnalysisStage.FAILED}
            if not 1 <= self.progress.percent <= 99 or self.progress.stage in invalid_stages:
                raise ValueError("A running analysis must have an active stage and 1-99% progress.")
        elif self.status is AnalysisStatus.COMPLETED:
            if self.progress.percent != 100 or self.progress.stage is not AnalysisStage.COMPLETED:
                raise ValueError(
                    "A completed analysis must be at the completed stage with 100% progress."
                )
            if self.result is None:
                raise ValueError("A completed analysis requires a result.")
        elif self.status is AnalysisStatus.FAILED:
            if self.progress.stage is not AnalysisStage.FAILED:
                raise ValueError("A failed analysis must be at the failed stage.")
            if self.error is None:
                raise ValueError("A failed analysis requires an error.")

        if self.status is not AnalysisStatus.COMPLETED and self.result is not None:
            raise ValueError("Only a completed analysis may include a result.")
        if self.status is not AnalysisStatus.FAILED and self.error is not None:
            raise ValueError("Only a failed analysis may include an error.")
        return self


class ReclusterRequest(StrictContract):
    """Ask for the same scenes to be grouped into a different zone count."""

    schema_version: ContractVersion = "1.0"
    analysis_id: UUID
    zone_count: ZoneCount
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def validate_requested_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "requested_at")


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"


class AgentSessionChannel(StrEnum):
    VOICE = "voice"
    TEXT = "text"


class AgentSession(StrictContract):
    """Conversation context for voice/text boundary and analysis assistance."""

    schema_version: ContractVersion = "1.0"
    id: UUID
    language: Literal["pt-BR", "en"]
    channel: AgentSessionChannel
    status: AgentSessionStatus
    field_id: UUID | None = None
    analysis_id: UUID | None = None
    turn_count: Annotated[int, PydanticField(ge=0)] = 0
    started_at: datetime
    updated_at: datetime
    expires_at: datetime

    @field_validator("started_at", "updated_at", "expires_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, getattr(info, "field_name", "timestamp"))

    @model_validator(mode="after")
    def validate_session_times(self) -> Self:
        if self.updated_at < self.started_at:
            raise ValueError("updated_at cannot be earlier than started_at.")
        if self.expires_at <= self.started_at:
            raise ValueError("expires_at must be later than started_at.")
        return self


class FeedbackRating(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"
    UNCLEAR = "unclear"


class Feedback(StrictContract):
    """Farmer feedback attached to an analysis and agent session."""

    schema_version: ContractVersion = "1.0"
    id: UUID
    analysis_id: UUID
    session_id: UUID
    rating: FeedbackRating
    comment: Annotated[str, PydanticField(min_length=1, max_length=500)] | None = None
    zone_id: Annotated[str, PydanticField(pattern=r"^zone-[1-7]$")] | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")
