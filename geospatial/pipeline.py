"""Asynchronous Sentinel acquisition and temporal-zone analysis pipeline."""

from __future__ import annotations

import importlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import escape
from math import cos, radians
from typing import Any

import numpy as np

from agriculture.domain import AnalysisStatus
from agriculture.observability import audit_event, get_audit_logger
from agriculture.ports.artifacts import ArtifactStore
from agriculture.ports.repositories import AgricultureRepository
from agriculture.schemas import (
    Analysis,
    AnalysisError,
    AnalysisMode,
    AnalysisProgress,
    AnalysisProvenance,
    AnalysisResult,
    AnalysisScope,
    AnalysisStage,
    GeoJSONPolygon,
    RelativeDevelopmentLabel,
    ResultArtifacts,
    SentinelScene,
    TrajectoryPoint,
    VegetationIndices,
    Zone,
)
from geospatial.cog import COGWindowReader, MultibandWindow
from geospatial.earth_search import EarthSearchClient, Sentinel2Scene
from geospatial.zoning import (
    InsufficientDataError,
    PixelTransform,
    ZoningResult,
    analyze_temporal_zones,
    build_valid_observation_mask,
    compute_spectral_indices,
)

PROVIDER_LABEL = "EU/ESA/Copernicus via Earth Search/AWS Open Data"
RUNNING_LEASE_SECONDS = 20 * 60
ZONE_COLORS = ("#b45309", "#d97706", "#65a30d", "#16a34a", "#0d9488", "#0284c7", "#4f46e5")
logger = get_audit_logger(__name__)


@dataclass(frozen=True, slots=True)
class LoadedObservation:
    scene: Sentinel2Scene
    window: MultibandWindow


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    analysis_id: str
    status: str
    scene_count: int = 0
    zone_count: int = 0
    error_code: str | None = None
    retryable: bool = False


class ObservationAcquisitionError(RuntimeError):
    """Real observations exist, but their raster pixels could not be acquired."""


class AnalysisPipeline:
    """Run the deterministic pixel pipeline and persist the strict API result."""

    def __init__(
        self,
        repository: AgricultureRepository,
        artifact_store: ArtifactStore,
        *,
        client: EarthSearchClient | None = None,
        reader: COGWindowReader | None = None,
        clock=lambda: datetime.now(UTC),
        target_scene_count: int = 6,
        max_dimension: int = 512,
    ) -> None:
        if not 2 <= target_scene_count <= 12:
            raise ValueError("target_scene_count must be between 2 and 12")
        if not 64 <= max_dimension <= 1024:
            raise ValueError("max_dimension must be between 64 and 1024")
        self.repository = repository
        self.artifact_store = artifact_store
        self.client = client or EarthSearchClient()
        self.reader = reader or COGWindowReader()
        self.clock = clock
        self.target_scene_count = target_scene_count
        self.max_dimension = max_dimension

    def run(self, analysis_id: str) -> PipelineOutcome:
        analysis = self.repository.get_analysis(str(analysis_id))
        if analysis is None:
            audit_event(
                logger,
                "sentinel_pipeline.not_found",
                level=logging.WARNING,
                component="worker",
                execution_id=str(analysis_id),
                analysis_id=str(analysis_id),
                status="not_found",
                error_code="ANALYSIS_NOT_FOUND",
            )
            raise KeyError(f"Analysis {analysis_id!r} does not exist.")
        if analysis.status is AnalysisStatus.COMPLETED:
            audit_event(
                logger,
                "sentinel_pipeline.skipped",
                component="worker",
                execution_id=str(analysis.id),
                analysis_id=str(analysis.id),
                field_id=str(analysis.field_id),
                status="already_completed",
                scene_count=len(analysis.result.scenes) if analysis.result else 0,
                zone_count=analysis.result.selected_zone_count if analysis.result else 0,
            )
            return PipelineOutcome(
                analysis_id=str(analysis.id),
                status="already_completed",
                scene_count=len(analysis.result.scenes) if analysis.result else 0,
                zone_count=analysis.result.selected_zone_count if analysis.result else 0,
            )
        if analysis.status is AnalysisStatus.FAILED and not (
            analysis.error and analysis.error.retryable
        ):
            audit_event(
                logger,
                "sentinel_pipeline.skipped",
                component="worker",
                execution_id=str(analysis.id),
                analysis_id=str(analysis.id),
                field_id=str(analysis.field_id),
                status="already_failed",
                error_code=analysis.error.code if analysis.error else None,
                retryable=False,
            )
            return PipelineOutcome(
                analysis_id=str(analysis.id),
                status="already_failed",
                error_code=analysis.error.code if analysis.error else None,
            )
        if analysis.status is AnalysisStatus.RUNNING:
            elapsed = self.clock() - analysis.updated_at
            if elapsed.total_seconds() < RUNNING_LEASE_SECONDS:
                audit_event(
                    logger,
                    "sentinel_pipeline.skipped",
                    component="worker",
                    execution_id=str(analysis.id),
                    analysis_id=str(analysis.id),
                    field_id=str(analysis.field_id),
                    status="already_running",
                    stage=analysis.progress.stage.value,
                    retryable=True,
                )
                return PipelineOutcome(
                    analysis_id=str(analysis.id),
                    status="already_running",
                    retryable=True,
                )

        audit_event(
            logger,
            "sentinel_pipeline.started",
            component="worker",
            execution_id=str(analysis.id),
            analysis_id=str(analysis.id),
            field_id=str(analysis.field_id),
            parent_analysis_id=(
                str(analysis.parent_analysis_id) if analysis.parent_analysis_id else None
            ),
            requested_zone_count=analysis.requested_zone_count,
            status="started",
        )
        field = self.repository.get_field(str(analysis.field_id))
        if field is None or not field.boundary_confirmed or field.boundary is None:
            return self._fail(
                analysis,
                code="FIELD_BOUNDARY_UNAVAILABLE",
                message="A confirmed field boundary is required for processing.",
                retryable=False,
            )

        try:
            analysis = self._advance(
                analysis,
                stage=AnalysisStage.ACQUIRING_SCENES,
                percent=10,
                message_pt="Buscando observações Sentinel-2 da safra.",
                message_en="Finding Sentinel-2 observations for the season.",
            )
            observations = self._load_observations(
                polygon=field.boundary.model_dump(mode="json"),
                start=field.season_start,
                end=field.season_end,
            )
            analysis = self._advance(
                analysis,
                stage=AnalysisStage.COMPUTING_INDICES,
                percent=45,
                message_pt="Calculando NDVI, NDRE e NDMI com máscara de nuvens.",
                message_en="Computing NDVI, NDRE and NDMI with cloud masking.",
            )
            bands = _stack_bands(observations)
            first_window = observations[0].window.bands["B04"]
            field_mask = _rasterize_field(
                field.boundary.model_dump(mode="json"),
                shape=first_window.data.shape,
                transform=first_window.transform,
                crs=first_window.crs,
            )
            scl = bands.pop("SCL")
            observation_mask = build_valid_observation_mask(
                bands,
                scl=scl,
                field_mask=field_mask,
            )
            indices = compute_spectral_indices(bands, valid_mask=observation_mask)
            observations, indices, observation_mask, discarded_scene_ids = (
                _stabilize_usable_observations(
                    observations,
                    indices,
                    observation_mask,
                )
            )
            if discarded_scene_ids:
                audit_event(
                    logger,
                    "sentinel_pipeline.scenes_discarded",
                    level=logging.WARNING,
                    component="worker",
                    execution_id=str(analysis.id),
                    analysis_id=str(analysis.id),
                    field_id=str(analysis.field_id),
                    status="discarded",
                    stage=AnalysisStage.COMPUTING_INDICES.value,
                    scene_count=len(discarded_scene_ids),
                    scene_ids=discarded_scene_ids,
                )
            if len(observations) < 2:
                raise InsufficientDataError(
                    "Fewer than two scenes contribute finite index values to pixels with at "
                    "least two observations."
                )

            scene_ids = tuple(item.scene.id for item in observations)
            audit_event(
                logger,
                "sentinel_pipeline.scenes_selected",
                component="worker",
                execution_id=str(analysis.id),
                analysis_id=str(analysis.id),
                field_id=str(analysis.field_id),
                status="selected",
                scene_count=len(observations),
                scene_ids=scene_ids,
            )
            reference_window = observations[0].window.bands["B04"]
            analysis = self._advance(
                analysis,
                stage=AnalysisStage.CLUSTERING_ZONES,
                percent=70,
                message_pt="Agrupando trajetórias espectrais relativas.",
                message_en="Grouping relative spectral trajectories.",
            )
            zoning = analyze_temporal_zones(
                indices=indices,
                field_mask=field_mask,
                requested_zone_count=analysis.requested_zone_count,
                scene_ids=scene_ids,
                pixel_area_m2=_pixel_area_m2(
                    reference_window.transform,
                    reference_window.crs,
                    reference_window.data.shape,
                ),
                transform=PixelTransform(
                    origin_x=reference_window.transform[2],
                    origin_y=reference_window.transform[5],
                    pixel_width=reference_window.transform[0],
                    pixel_height=reference_window.transform[4],
                    crs=reference_window.crs,
                ),
            )
            analysis = self._advance(
                analysis,
                stage=AnalysisStage.GENERATING_EXPLANATION,
                percent=90,
                message_pt="Gerando evidências e artefatos auditáveis.",
                message_en="Generating auditable evidence and artifacts.",
            )
            result = self._build_result(
                analysis=analysis,
                observations=observations,
                indices=indices,
                observation_mask=observation_mask,
                zoning=zoning,
                source_crs=reference_window.crs,
            )
            completed_at = self.clock()
            completed = Analysis(
                id=analysis.id,
                field_id=analysis.field_id,
                parent_analysis_id=analysis.parent_analysis_id,
                status=AnalysisStatus.COMPLETED,
                requested_zone_count=analysis.requested_zone_count,
                progress=AnalysisProgress(
                    percent=100,
                    stage=AnalysisStage.COMPLETED,
                    message_pt="Análise concluída.",
                    message_en="Analysis completed.",
                    updated_at=completed_at,
                ),
                result=result,
                error=None,
                created_at=analysis.created_at,
                updated_at=completed_at,
            )
            self.repository.save_analysis(completed)
            audit_event(
                logger,
                "sentinel_pipeline.completed",
                component="worker",
                execution_id=str(completed.id),
                analysis_id=str(completed.id),
                field_id=str(completed.field_id),
                status="completed",
                stage=AnalysisStage.COMPLETED.value,
                percent=100,
                scene_count=len(result.scenes),
                scene_ids=tuple(scene.scene_id for scene in result.scenes),
                zone_count=result.selected_zone_count,
            )
            return PipelineOutcome(
                analysis_id=str(completed.id),
                status="completed",
                scene_count=len(result.scenes),
                zone_count=result.selected_zone_count,
            )
        except InsufficientDataError as exc:
            return self._fail(
                analysis,
                code="INSUFFICIENT_SATELLITE_DATA",
                message=str(exc),
                retryable=False,
                error_type=type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001 - task state must be persisted before returning
            return self._fail(
                analysis,
                code="ANALYSIS_PIPELINE_FAILED",
                message=f"{type(exc).__name__}: {exc}",
                retryable=True,
                error_type=type(exc).__name__,
            )

    def _load_observations(
        self,
        *,
        polygon: dict[str, Any],
        start: date,
        end: date,
    ) -> tuple[LoadedObservation, ...]:
        scenes = self.client.search_scenes(
            polygon=polygon,
            start=start,
            end=end,
            max_cloud_cover=45.0,
            limit=100,
        )
        grouped: dict[date, list[Sentinel2Scene]] = defaultdict(list)
        for scene in scenes:
            if all(name in scene.assets for name in ("red", "rededge1", "nir", "swir16", "scl")):
                grouped[scene.captured_at.date()].append(scene)
        candidate_dates = _evenly_spaced_dates(
            sorted(grouped),
            min(len(grouped), self.target_scene_count * 2),
        )
        bbox = _polygon_bbox(polygon)
        loaded: list[LoadedObservation] = []
        reference_grid = None
        for captured_date in candidate_dates:
            day_scenes = sorted(
                grouped[captured_date],
                key=lambda scene: scene.cloud_cover if scene.cloud_cover is not None else 101.0,
            )
            for scene in day_scenes[:3]:
                try:
                    window = self.reader.read_required_bands(
                        scene.assets,
                        bbox_wgs84=bbox,
                        max_dimension=self.max_dimension,
                        reference_grid=reference_grid,
                        calibration=scene.asset_calibration,
                    )
                    coverage = np.mean(
                        np.logical_and.reduce([band.valid_mask for band in window.bands.values()])
                    )
                    if coverage < 0.95:
                        continue
                except Exception:  # noqa: BLE001 - another tile/date may cover the field
                    continue
                reference_grid = window.bands["B04"]
                loaded.append(LoadedObservation(scene=scene, window=window))
                break
        loaded.sort(key=lambda item: item.scene.captured_at)
        if len(loaded) < 2:
            if len(candidate_dates) >= 2:
                raise ObservationAcquisitionError(
                    "Sentinel observations were found, but fewer than two aligned raster "
                    "windows could be acquired."
                )
            raise InsufficientDataError(
                "At least two dates with complete, aligned Sentinel bands are required."
            )
        return _evenly_spaced_observations(loaded, self.target_scene_count)

    def _advance(
        self,
        analysis: Analysis,
        *,
        stage: AnalysisStage,
        percent: int,
        message_pt: str,
        message_en: str,
    ) -> Analysis:
        now = self.clock()
        running = Analysis(
            id=analysis.id,
            field_id=analysis.field_id,
            parent_analysis_id=analysis.parent_analysis_id,
            status=AnalysisStatus.RUNNING,
            requested_zone_count=analysis.requested_zone_count,
            progress=AnalysisProgress(
                percent=percent,
                stage=stage,
                message_pt=message_pt,
                message_en=message_en,
                updated_at=now,
            ),
            result=None,
            error=None,
            created_at=analysis.created_at,
            updated_at=now,
        )
        saved = self.repository.save_analysis(running)
        audit_event(
            logger,
            "sentinel_pipeline.stage",
            component="worker",
            execution_id=str(saved.id),
            analysis_id=str(saved.id),
            field_id=str(saved.field_id),
            status="running",
            stage=stage.value,
            percent=percent,
        )
        return saved

    def _fail(
        self,
        analysis: Analysis,
        *,
        code: str,
        message: str,
        retryable: bool,
        error_type: str | None = None,
    ) -> PipelineOutcome:
        now = self.clock()
        failed = Analysis(
            id=analysis.id,
            field_id=analysis.field_id,
            parent_analysis_id=analysis.parent_analysis_id,
            status=AnalysisStatus.FAILED,
            requested_zone_count=analysis.requested_zone_count,
            progress=AnalysisProgress(
                percent=analysis.progress.percent,
                stage=AnalysisStage.FAILED,
                message_pt="Não foi possível concluir esta análise.",
                message_en="This analysis could not be completed.",
                updated_at=now,
            ),
            result=None,
            error=AnalysisError(
                code=code,
                message=message[:500],
                retryable=retryable,
                occurred_at=now,
            ),
            created_at=analysis.created_at,
            updated_at=now,
        )
        self.repository.save_analysis(failed)
        audit_event(
            logger,
            "sentinel_pipeline.failed",
            level=logging.ERROR,
            component="worker",
            execution_id=str(failed.id),
            analysis_id=str(failed.id),
            field_id=str(failed.field_id),
            status="failed",
            stage=AnalysisStage.FAILED.value,
            percent=failed.progress.percent,
            error_code=code,
            error_type=error_type,
            retryable=retryable,
        )
        return PipelineOutcome(
            analysis_id=str(failed.id),
            status="failed",
            error_code=code,
            retryable=retryable,
        )

    def _build_result(
        self,
        *,
        analysis: Analysis,
        observations: tuple[LoadedObservation, ...],
        indices: dict[str, np.ndarray],
        observation_mask: np.ndarray,
        zoning: ZoningResult,
        source_crs: str,
    ) -> AnalysisResult:
        prefix = f"analyses/{analysis.id}"
        api_scenes: list[SentinelScene] = []
        for time_index, observation in enumerate(observations):
            means = _index_means(indices, observation_mask, time_index)
            preview = self.artifact_store.put_bytes(
                f"{prefix}/scenes/{observation.scene.id}-ndvi.svg",
                _index_svg(indices["NDVI"][time_index], observation_mask[time_index]),
                content_type="image/svg+xml",
            )
            api_scenes.append(
                SentinelScene(
                    scene_id=observation.scene.id,
                    captured_at=observation.scene.captured_at,
                    cloud_cover_percent=(
                        observation.scene.cloud_cover
                        if observation.scene.cloud_cover is not None
                        else 100.0
                    ),
                    field_indices=VegetationIndices(**means),
                    preview_uri=preview.uri,
                )
            )

        captured_by_id = {scene.scene_id: scene.captured_at for scene in api_scenes}
        api_zones = tuple(
            Zone(
                zone_id=zone.zone_id,
                relative_label=RelativeDevelopmentLabel(zone.relative_label),
                area_ha=zone.area_ha,
                area_percent=zone.area_percent,
                boundary=_zone_polygon(zone.geometry, source_crs=source_crs),
                trajectory=tuple(
                    TrajectoryPoint(
                        scene_id=point.scene_id,
                        captured_at=captured_by_id[point.scene_id],
                        indices=VegetationIndices(
                            ndvi=point.ndvi,
                            ndre=point.ndre,
                            ndmi=point.ndmi,
                        ),
                    )
                    for point in zone.trajectory
                ),
                summary_pt=zone.summary_pt,
                summary_en=zone.summary_en,
            )
            for zone in zoning.zones
        )
        exact_geojson = dict(zoning.feature_collection)
        exact_geojson["metadata"] = {
            **exact_geojson.get("metadata", {}),
            "scope": zoning.scope,
            "selection": {
                "mode": zoning.selection.mode,
                "reason_pt": zoning.selection.reason_pt,
                "reason_en": zoning.selection.reason_en,
            },
        }
        zone_artifact = self.artifact_store.put_bytes(
            f"{prefix}/zones.geojson",
            json.dumps(exact_geojson, ensure_ascii=False).encode(),
            content_type="application/geo+json",
        )
        map_artifact = self.artifact_store.put_bytes(
            f"{prefix}/zone-map.svg",
            _zone_svg(zoning.label_grid),
            content_type="image/svg+xml",
        )
        report_artifact = self.artifact_store.put_bytes(
            f"{prefix}/processing-report.json",
            json.dumps(
                {
                    "analysis_id": str(analysis.id),
                    "scene_ids": [item.scene.id for item in observations],
                    "quality": zoning.to_dict()["quality"],
                    "selection": zoning.to_dict()["selection"],
                    "scope": zoning.scope,
                    "provider": PROVIDER_LABEL,
                },
                ensure_ascii=False,
            ).encode(),
            content_type="application/json",
        )
        return AnalysisResult(
            selected_zone_count=zoning.selected_zone_count,
            mode=AnalysisMode.LIVE,
            generated_at=self.clock(),
            scenes=tuple(api_scenes),
            zones=api_zones,
            scope=AnalysisScope(
                disclaimer_pt=zoning.scope["disclaimer_pt"],
                disclaimer_en=zoning.scope["disclaimer_en"],
            ),
            provenance=AnalysisProvenance(
                provider=PROVIDER_LABEL,
                mission="Sentinel-2",
                product_level="L2A",
                bands=("B04", "B05", "B08", "B11", "SCL"),
                indices=("NDVI", "NDRE", "NDMI"),
                scene_ids=tuple(scene.scene_id for scene in api_scenes),
                processing_version="0.2.0",
            ),
            artifacts=ResultArtifacts(
                map_preview_uri=map_artifact.uri,
                zone_geojson_uri=zone_artifact.uri,
                report_uri=report_artifact.uri,
            ),
        )


def _evenly_spaced_dates(values: list[date], count: int) -> tuple[date, ...]:
    if len(values) <= count:
        return tuple(values)
    indexes = [round(index * (len(values) - 1) / (count - 1)) for index in range(count)]
    return tuple(values[index] for index in indexes)


def _evenly_spaced_observations(
    values: list[LoadedObservation], count: int
) -> tuple[LoadedObservation, ...]:
    if len(values) <= count:
        return tuple(values)
    indexes = [round(index * (len(values) - 1) / (count - 1)) for index in range(count)]
    return tuple(values[index] for index in indexes)


def _polygon_bbox(polygon: dict[str, Any]) -> tuple[float, float, float, float]:
    ring = polygon["coordinates"][0]
    longitudes = [position[0] for position in ring]
    latitudes = [position[1] for position in ring]
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def _stack_bands(observations: tuple[LoadedObservation, ...]) -> dict[str, np.ndarray]:
    names = ("B04", "B05", "B08", "B11", "SCL")
    return {
        name: np.stack([item.window.bands[name].data for item in observations]) for name in names
    }


def _stabilize_usable_observations(
    observations: tuple[LoadedObservation, ...],
    indices: dict[str, np.ndarray],
    observation_mask: np.ndarray,
) -> tuple[
    tuple[LoadedObservation, ...],
    dict[str, np.ndarray],
    np.ndarray,
    tuple[str, ...],
]:
    """Drop scenes that cannot contribute to the shared temporal pixel set.

    A useful pixel has finite NDVI, NDRE and NDMI in at least two observations.  A
    retained scene must contribute all three finite indices to at least one such
    pixel.  The support is recomputed after every deterministic batch removal so
    observations, index arrays and masks remain aligned even if the rule evolves.
    """

    required_indices = ("NDVI", "NDRE", "NDMI")
    missing = [name for name in required_indices if name not in indices]
    if missing:
        raise ValueError(f"Missing required indices: {', '.join(missing)}.")

    current_observations = observations
    current_indices = {name: np.asarray(values) for name, values in indices.items()}
    current_mask = np.asarray(observation_mask, dtype=bool)
    if current_mask.ndim != 3:
        raise ValueError("observation_mask must use (time, row, column) order.")
    if current_mask.shape[0] != len(current_observations):
        raise ValueError("observations and observation_mask must have the same time dimension.")
    if any(values.shape != current_mask.shape for values in current_indices.values()):
        raise ValueError("indices and observation_mask must have identical shapes.")

    discarded: list[str] = []
    while current_observations:
        observation_valid = current_mask & np.logical_and.reduce(
            [np.isfinite(current_indices[name]) for name in required_indices]
        )
        shared_pixels = observation_valid.sum(axis=0) >= 2
        contributes = np.any(
            observation_valid & shared_pixels[np.newaxis, :, :],
            axis=(1, 2),
        )
        if bool(contributes.all()):
            break

        discarded.extend(
            observation.scene.id
            for observation, keep in zip(current_observations, contributes, strict=True)
            if not bool(keep)
        )
        keep_indices = np.flatnonzero(contributes)
        current_observations = tuple(current_observations[int(index)] for index in keep_indices)
        current_indices = {name: values[keep_indices] for name, values in current_indices.items()}
        current_mask = current_mask[keep_indices]

    return current_observations, current_indices, current_mask, tuple(discarded)


def _pixel_area_m2(
    transform: tuple[float, float, float, float, float, float],
    crs: str,
    shape: tuple[int, int],
) -> float:
    if crs.upper() in {"EPSG:4326", "OGC:CRS84"}:
        center_latitude = transform[5] + transform[4] * shape[0] / 2
        width_m = abs(transform[0]) * 111_320.0 * abs(cos(radians(center_latitude)))
        height_m = abs(transform[4]) * 110_574.0
        return width_m * height_m
    return abs(transform[0] * transform[4])


def _rasterize_field(
    polygon: dict[str, Any],
    *,
    shape: tuple[int, int],
    transform: tuple[float, float, float, float, float, float],
    crs: str,
) -> np.ndarray:
    rasterio = importlib.import_module("rasterio")
    features = importlib.import_module("rasterio.features")
    warp = importlib.import_module("rasterio.warp")
    geometry = warp.transform_geom("EPSG:4326", crs, polygon)
    return features.geometry_mask(
        [geometry],
        out_shape=shape,
        transform=rasterio.Affine(*transform),
        invert=True,
        all_touched=False,
    )


def _index_means(
    indices: dict[str, np.ndarray], mask: np.ndarray, time_index: int
) -> dict[str, float]:
    result: dict[str, float] = {}
    for wire_name, index_name in (("ndvi", "NDVI"), ("ndre", "NDRE"), ("ndmi", "NDMI")):
        values = indices[index_name][time_index][mask[time_index]]
        finite = values[np.isfinite(values)]
        if not len(finite):
            raise InsufficientDataError(f"Scene has no valid {index_name} pixels inside the field.")
        result[wire_name] = round(float(np.mean(finite)), 6)
    return result


def _zone_polygon(geometry: dict[str, Any], *, source_crs: str) -> GeoJSONPolygon:
    warp = importlib.import_module("rasterio.warp")
    shapely_geometry = importlib.import_module("shapely.geometry")
    shapely_ops = importlib.import_module("shapely.ops")
    source = shapely_geometry.shape(geometry)
    if source.geom_type == "MultiPolygon":
        source = shapely_ops.unary_union(source.geoms)
    if source.geom_type == "MultiPolygon":
        source = max(source.geoms, key=lambda item: item.area)
    transformed = warp.transform_geom(source_crs, "EPSG:4326", shapely_geometry.mapping(source))
    polygon = shapely_geometry.shape(transformed)
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda item: item.area)
    polygon = shapely_geometry.Polygon(polygon.exterior)
    tolerance = 0.0
    while len(polygon.exterior.coords) > 200:
        tolerance = tolerance * 2 or 1e-7
        polygon = polygon.simplify(tolerance, preserve_topology=True)
    ring = tuple((float(x), float(y)) for x, y in polygon.exterior.coords)
    return GeoJSONPolygon(coordinates=(ring,))


def _zone_svg(label_grid: tuple[tuple[int | None, ...], ...]) -> bytes:
    height = len(label_grid)
    width = len(label_grid[0])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{escape("Mapa de zonas relativas")}">',
        f'<rect width="{width}" height="{height}" fill="#e5e7eb"/>',
    ]
    for row_index, row in enumerate(label_grid):
        start = 0
        while start < width:
            label = row[start]
            end = start + 1
            while end < width and row[end] == label:
                end += 1
            if label is not None:
                color = ZONE_COLORS[label - 1]
                parts.append(
                    f'<rect x="{start}" y="{row_index}" width="{end - start}" '
                    f'height="1" fill="{color}"/>'
                )
            start = end
    parts.append("</svg>")
    return "".join(parts).encode()


def _index_svg(values: np.ndarray, valid_mask: np.ndarray) -> bytes:
    height, width = values.shape
    quantized = np.full(values.shape, -1, dtype=np.int16)
    clipped = np.clip(values, -1.0, 1.0)
    quantized[valid_mask & np.isfinite(clipped)] = np.rint(
        (clipped[valid_mask & np.isfinite(clipped)] + 1.0) * 7.5
    ).astype(np.int16)
    palette = (
        "#7f1d1d",
        "#991b1b",
        "#b45309",
        "#d97706",
        "#eab308",
        "#84cc16",
        "#65a30d",
        "#4d7c0f",
        "#3f6212",
        "#166534",
        "#15803d",
        "#16a34a",
        "#059669",
        "#0d9488",
        "#0f766e",
        "#115e59",
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        'role="img" aria-label="Prévia NDVI"><rect width="100%" height="100%" fill="#d1d5db"/>'
    ]
    for row_index, row in enumerate(quantized):
        start = 0
        while start < width:
            value = int(row[start])
            end = start + 1
            while end < width and int(row[end]) == value:
                end += 1
            if value >= 0:
                parts.append(
                    f'<rect x="{start}" y="{row_index}" width="{end - start}" '
                    f'height="1" fill="{palette[value]}"/>'
                )
            start = end
    parts.append("</svg>")
    return "".join(parts).encode()
