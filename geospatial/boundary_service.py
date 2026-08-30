"""Real Sentinel-backed boundary suggestions with a safe geometric fallback."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from math import ceil, cos, floor, hypot, pi, radians, sqrt
from typing import Any

import numpy as np
from rasterio import Affine
from rasterio.crs import CRS
from rasterio.warp import transform as transform_coordinates

from agriculture.ports.boundaries import BoundaryProposal
from agriculture.schemas import BoundarySource, Field, GeoJSONPolygon
from geospatial.boundary import BoundarySuggestionResult, suggest_boundaries
from geospatial.cog import COGWindowReader, MultibandWindow, RasterWindow
from geospatial.earth_search import EarthSearchClient, Sentinel2Scene
from geospatial.provenance import provenance_payload
from geospatial.tools import resolve_required_band_assets

_REQUIRED_BANDS = frozenset({"B04", "B05", "B08", "B11", "SCL"})
_MASKED_SCL_CLASSES = np.asarray((0, 1, 3, 8, 9, 10, 11), dtype=np.int16)
_MINIMUM_LOCAL_CLEAR_COVERAGE = 0.60
_CATALOG_SCENE_LIMIT = 12


@dataclass(frozen=True, slots=True)
class AcquiredBoundarySuggestion:
    result: BoundarySuggestionResult
    scene_id: str | None
    captured_at: datetime | None
    cloud_cover: float | None
    bbox: tuple[float, float, float, float]
    provenance: dict[str, str]
    debug: dict[str, Any] = field(default_factory=dict)


class _SceneRejected(ValueError):
    def __init__(self, reason: str, *, local_clear_coverage: float | None = None) -> None:
        self.reason = reason
        self.local_clear_coverage = local_clear_coverage
        super().__init__(reason)


def _search_bbox(
    reference_location: tuple[float, float], estimated_area_ha: float
) -> tuple[float, float, float, float]:
    longitude, latitude = reference_location
    half_side_m = min(8_000.0, max(300.0, sqrt(estimated_area_ha * 10_000.0) * 1.25))
    latitude_delta = half_side_m / 110_574.0
    longitude_delta = half_side_m / max(111_320.0 * abs(cos(radians(latitude))), 1_000.0)
    return (
        longitude - longitude_delta,
        latitude - latitude_delta,
        longitude + longitude_delta,
        latitude + latitude_delta,
    )


def _rank_scenes(scenes: tuple[Sentinel2Scene, ...]) -> tuple[Sentinel2Scene, ...]:
    usable = [
        scene
        for scene in scenes
        if _REQUIRED_BANDS <= resolve_required_band_assets(scene.assets).keys()
    ]
    return tuple(
        sorted(
            usable,
            key=lambda scene: (
                scene.cloud_cover if scene.cloud_cover is not None else 101.0,
                -scene.captured_at.timestamp(),
            ),
        )
    )


def _reference_pixel(
    reference_location: tuple[float, float], raster: RasterWindow
) -> tuple[int, int]:
    try:
        x_values, y_values = transform_coordinates(
            "EPSG:4326",
            raster.crs,
            [reference_location[0]],
            [reference_location[1]],
        )
        column_float, row_float = (~Affine(*raster.transform)) @ (x_values[0], y_values[0])
    except Exception as exc:  # noqa: BLE001 - normalize CRS/transform failures for scene retry
        raise _SceneRejected("reference_location_could_not_be_georeferenced") from exc
    row, column = floor(row_float), floor(column_float)
    height, width = raster.data.shape
    if not (0 <= row < height and 0 <= column < width):
        raise _SceneRejected("reference_location_outside_scene_window")
    return row, column


def _pixel_resolution_m(raster: RasterWindow, *, latitude: float) -> float:
    affine = Affine(*raster.transform)
    if abs(affine.determinant) <= 1e-12:
        raise _SceneRejected("invalid_raster_transform")
    crs = CRS.from_user_input(raster.crs)
    if crs.is_projected:
        units = crs.linear_units_factor
        unit_factor = float(units[1] if isinstance(units, tuple) else units)
        resolution = sqrt(abs(affine.determinant)) * unit_factor
    else:
        longitude_metres = max(111_320.0 * abs(cos(radians(latitude))), 1_000.0)
        latitude_metres = 110_574.0
        column_size = hypot(affine.a * longitude_metres, affine.d * latitude_metres)
        row_size = hypot(affine.b * longitude_metres, affine.e * latitude_metres)
        resolution = sqrt(column_size * row_size)
    if not np.isfinite(resolution) or resolution <= 0:
        raise _SceneRejected("invalid_raster_resolution")
    return float(resolution)


def _normalised_difference(
    first: np.ndarray,
    second: np.ndarray,
    valid: np.ndarray,
) -> np.ndarray:
    denominator = first + second
    result = np.full(first.shape, np.nan, dtype=np.float64)
    np.divide(first - second, denominator, out=result, where=valid & (denominator != 0))
    return result


def _local_clear_coverage(
    valid: np.ndarray,
    *,
    reference_pixel: tuple[int, int],
    estimated_area_ha: float,
    resolution_m: float,
) -> float:
    target_pixels = max(9.0, estimated_area_ha * 10_000.0 / resolution_m**2)
    radius = min(max(2, ceil(sqrt(target_pixels / pi))), 24)
    row, column = reference_pixel
    row_start, row_end = max(0, row - radius), min(valid.shape[0], row + radius + 1)
    col_start, col_end = max(0, column - radius), min(valid.shape[1], column + radius + 1)
    local = valid[row_start:row_end, col_start:col_end]
    if local.size == 0:
        return 0.0
    return float(np.mean(local))


def _spectral_features(
    window: MultibandWindow,
    *,
    reference_pixel: tuple[int, int],
    estimated_area_ha: float,
    resolution_m: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    if not _REQUIRED_BANDS <= window.bands.keys():
        raise _SceneRejected("scene_missing_required_bands")
    bands = {name: window.bands[name] for name in _REQUIRED_BANDS}
    expected_shape = bands["B04"].data.shape
    if any(band.data.shape != expected_shape for band in bands.values()):
        raise _SceneRejected("required_bands_are_not_aligned")

    valid = np.ones(expected_shape, dtype=bool)
    for band in bands.values():
        valid &= band.valid_mask & np.isfinite(band.data)
    scl_data = bands["SCL"].data
    scl = np.where(np.isfinite(scl_data), np.rint(scl_data), 0).astype(np.int16, copy=False)
    valid &= ~np.isin(scl, _MASKED_SCL_CLASSES)

    red = bands["B04"].data.astype(np.float64, copy=False)
    red_edge = bands["B05"].data.astype(np.float64, copy=False)
    nir = bands["B08"].data.astype(np.float64, copy=False)
    swir = bands["B11"].data.astype(np.float64, copy=False)
    reflectance = (red, red_edge, nir, swir)
    valid &= np.logical_and.reduce([band >= 0 for band in reflectance])
    valid &= np.logical_or.reduce([band > 0 for band in reflectance])
    ndvi = _normalised_difference(nir, red, valid)
    ndre = _normalised_difference(nir, red_edge, valid)
    ndmi = _normalised_difference(nir, swir, valid)
    valid &= np.isfinite(ndvi) & np.isfinite(ndre) & np.isfinite(ndmi)

    coverage = _local_clear_coverage(
        valid,
        reference_pixel=reference_pixel,
        estimated_area_ha=estimated_area_ha,
        resolution_m=resolution_m,
    )
    if coverage < _MINIMUM_LOCAL_CLEAR_COVERAGE:
        raise _SceneRejected("insufficient_local_clear_coverage", local_clear_coverage=coverage)
    features = np.stack((red, red_edge, nir, swir, ndvi, ndre, ndmi), axis=2)
    return features, valid, coverage


class SentinelBoundaryService:
    def __init__(
        self,
        *,
        client: EarthSearchClient | None = None,
        reader: COGWindowReader | None = None,
        max_scenes_to_try: int = 4,
    ) -> None:
        if not 1 <= max_scenes_to_try <= 8:
            raise ValueError("max_scenes_to_try must be between 1 and 8")
        self.client = client or EarthSearchClient()
        self.reader = reader or COGWindowReader()
        self.max_scenes_to_try = max_scenes_to_try

    def suggest(
        self,
        *,
        reference_location: tuple[float, float],
        estimated_area_ha: float,
        season_start: date,
        season_end: date,
    ) -> AcquiredBoundarySuggestion:
        if season_end <= season_start:
            raise ValueError("season_end must be after season_start")
        if (season_end - season_start).days > 365:
            raise ValueError("crop season cannot exceed 365 days")
        bbox = _search_bbox(reference_location, estimated_area_ha)
        debug: dict[str, Any] = {
            "season_start": season_start.isoformat(),
            "season_end": season_end.isoformat(),
            "catalog_scene_limit": _CATALOG_SCENE_LIMIT,
            "scene_attempt_limit": self.max_scenes_to_try,
            "masked_scl_classes": _MASKED_SCL_CLASSES.tolist(),
            "minimum_local_clear_coverage": _MINIMUM_LOCAL_CLEAR_COVERAGE,
            "attempts": [],
        }
        try:
            scenes = self.client.search(
                bbox=bbox,
                start=season_start,
                end=season_end,
                max_cloud_cover=35.0,
                limit=_CATALOG_SCENE_LIMIT,
            )
        except Exception as exc:  # noqa: BLE001 - external catalog failure uses editable fallback
            debug["catalog_error"] = type(exc).__name__
            return self._fallback(
                bbox=bbox,
                reference_location=reference_location,
                estimated_area_ha=estimated_area_ha,
                reason="sentinel_imagery_temporarily_unavailable",
                debug=debug,
            )

        ranked_scenes = _rank_scenes(tuple(scenes))[: self.max_scenes_to_try]
        debug["catalog_scene_count"] = len(scenes)
        debug["eligible_scene_count"] = len(ranked_scenes)
        if not ranked_scenes:
            return self._fallback(
                bbox=bbox,
                reference_location=reference_location,
                estimated_area_ha=estimated_area_ha,
                reason="no_sentinel_scene_with_required_bands",
                debug=debug,
            )

        attempts: list[dict[str, Any]] = debug["attempts"]
        for scene in ranked_scenes:
            attempt: dict[str, Any] = {"scene_id": scene.id, "cloud_cover": scene.cloud_cover}
            attempts.append(attempt)
            try:
                multiband = self.reader.read_required_bands(
                    scene.assets,
                    bbox_wgs84=bbox,
                    max_dimension=768,
                    calibration=scene.asset_calibration,
                )
                reference_raster = multiband.bands["B04"]
                reference_pixel = _reference_pixel(reference_location, reference_raster)
                resolution_m = _pixel_resolution_m(
                    reference_raster,
                    latitude=reference_location[1],
                )
                features, valid, local_coverage = _spectral_features(
                    multiband,
                    reference_pixel=reference_pixel,
                    estimated_area_ha=estimated_area_ha,
                    resolution_m=resolution_m,
                )
                attempt["local_clear_coverage"] = round(local_coverage, 6)
                result = suggest_boundaries(
                    features,
                    reference_location=reference_location,
                    resolution_m=resolution_m,
                    estimated_area_ha=estimated_area_ha,
                    valid_mask=valid,
                    reference_pixel=reference_pixel,
                    transform=reference_raster.transform,
                    crs=reference_raster.crs,
                )
                if result.used_fallback:
                    attempt.update(status="fallback", reason=result.reason)
                    continue
            except _SceneRejected as exc:
                attempt.update(status="rejected", reason=exc.reason)
                if exc.local_clear_coverage is not None:
                    attempt["local_clear_coverage"] = round(exc.local_clear_coverage, 6)
                continue
            except Exception as exc:  # noqa: BLE001 - a bad tile must not block the next tile
                attempt.update(status="error", reason=type(exc).__name__)
                continue

            attempt["status"] = "selected"
            return AcquiredBoundarySuggestion(
                result=result,
                scene_id=scene.id,
                captured_at=scene.captured_at,
                cloud_cover=scene.cloud_cover,
                bbox=bbox,
                provenance=provenance_payload(),
                debug=debug,
            )

        return self._fallback(
            bbox=bbox,
            reference_location=reference_location,
            estimated_area_ha=estimated_area_ha,
            reason="no_sentinel_scene_produced_a_boundary",
            debug=debug,
        )

    @staticmethod
    def _fallback(
        *,
        bbox: tuple[float, float, float, float],
        reference_location: tuple[float, float],
        estimated_area_ha: float,
        reason: str,
        debug: dict[str, Any],
    ) -> AcquiredBoundarySuggestion:
        fallback = suggest_boundaries(
            np.ones((4, 4), dtype=np.float64),
            reference_location=reference_location,
            resolution_m=10.0,
            estimated_area_ha=estimated_area_ha,
        )
        fallback = replace(fallback, reason=reason)
        return AcquiredBoundarySuggestion(
            result=fallback,
            scene_id=None,
            captured_at=None,
            cloud_cover=None,
            bbox=bbox,
            provenance=provenance_payload(),
            debug=debug,
        )


class EarthSearchBoundaryProvider:
    """Adapter from the geospatial engine to the stable singular PR2 API contract."""

    def __init__(self, service: SentinelBoundaryService | None = None) -> None:
        self.service = service or SentinelBoundaryService()

    def suggest(self, field: Field) -> BoundaryProposal:
        acquired = self.service.suggest(
            reference_location=field.reference_location.coordinates,
            estimated_area_ha=field.estimated_area_ha,
            season_start=field.season_start,
            season_end=field.season_end,
        )
        candidate = acquired.result.candidates[0]
        raw_coordinates = candidate.boundary["coordinates"]
        coordinates = tuple(tuple(tuple(position) for position in ring) for ring in raw_coordinates)
        boundary = GeoJSONPolygon(coordinates=coordinates)
        confidence = (
            min(candidate.scores.total, 0.20)
            if acquired.result.used_fallback
            else candidate.scores.total
        )
        return BoundaryProposal(
            boundary=boundary,
            estimated_area_ha=candidate.estimated_area_ha,
            confidence=confidence,
            source=(
                BoundarySource.HYBRID
                if acquired.result.used_fallback
                else BoundarySource.SENTINEL_2
            ),
        )


def suggestion_debug_payload(acquired: AcquiredBoundarySuggestion) -> dict[str, Any]:
    """Return auditable details for logs/artifacts without changing the public API."""

    return {
        "scene_id": acquired.scene_id,
        "captured_at": acquired.captured_at.isoformat() if acquired.captured_at else None,
        "cloud_cover": acquired.cloud_cover,
        "bbox": list(acquired.bbox),
        "provenance": acquired.provenance,
        "used_fallback": acquired.result.used_fallback,
        "reason": acquired.result.reason,
        "debug": acquired.debug,
        "candidates": [
            {
                "rank": candidate.rank,
                "boundary": candidate.boundary,
                "estimated_area_ha": candidate.estimated_area_ha,
                "scores": candidate.scores.as_dict(),
                "rationale": list(candidate.rationale),
            }
            for candidate in acquired.result.candidates
        ],
    }
