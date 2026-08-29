"""Real Sentinel-backed boundary suggestions with a safe geometric fallback."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import cos, radians, sqrt
from typing import Any

import numpy as np

from agriculture.ports.boundaries import BoundaryProposal
from agriculture.schemas import BoundarySource, Field, GeoJSONPolygon
from geospatial.boundary import BoundarySuggestionResult, suggest_boundaries
from geospatial.cog import COGWindowReader, RasterWindow
from geospatial.earth_search import EarthSearchClient, Sentinel2Scene
from geospatial.provenance import provenance_payload


@dataclass(frozen=True, slots=True)
class AcquiredBoundarySuggestion:
    result: BoundarySuggestionResult
    scene_id: str | None
    captured_at: datetime | None
    cloud_cover: float | None
    bbox: tuple[float, float, float, float]
    provenance: dict[str, str]


def _search_bbox(
    reference_location: tuple[float, float], estimated_area_ha: float
) -> tuple[float, float, float, float]:
    longitude, latitude = reference_location
    # Give region growing enough context to observe edges around the farmer's
    # estimated field. The ceiling still keeps the COG request tightly bounded.
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
    usable = [scene for scene in scenes if scene.assets.get("red") and scene.assets.get("nir")]
    return tuple(
        sorted(
            usable,
            key=lambda scene: (
                scene.cloud_cover if scene.cloud_cover is not None else 101.0,
                -scene.captured_at.timestamp(),
            ),
        )
    )


def _spectral_features(red: RasterWindow, nir: RasterWindow) -> tuple[np.ndarray, np.ndarray]:
    if red.data.shape != nir.data.shape:
        raise ValueError("Red and NIR windows must have the same shape.")
    valid = red.valid_mask & nir.valid_mask & np.isfinite(red.data) & np.isfinite(nir.data)
    denominator = nir.data + red.data
    ndvi = np.full(red.data.shape, np.nan, dtype=np.float64)
    np.divide(nir.data - red.data, denominator, out=ndvi, where=valid & (denominator != 0))
    valid &= np.isfinite(ndvi)
    features = np.stack((red.data, nir.data, ndvi), axis=2)
    return features, valid


class SentinelBoundaryService:
    def __init__(
        self,
        *,
        client: EarthSearchClient | None = None,
        reader: COGWindowReader | None = None,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.client = client or EarthSearchClient()
        self.reader = reader or COGWindowReader()
        self.clock = clock

    def suggest(
        self,
        *,
        reference_location: tuple[float, float],
        estimated_area_ha: float,
        lookback_days: int = 120,
    ) -> AcquiredBoundarySuggestion:
        if not 30 <= lookback_days <= 366:
            raise ValueError("lookback_days must be between 30 and 366")
        bbox = _search_bbox(reference_location, estimated_area_ha)
        end = self.clock()
        start = end - timedelta(days=lookback_days)
        try:
            scenes = self.client.search(
                bbox=bbox,
                start=start,
                end=end,
                max_cloud_cover=35.0,
                limit=40,
            )
            ranked_scenes = _rank_scenes(scenes)
            if not ranked_scenes:
                raise ValueError("No scene contains both red and NIR assets.")
            for scene in ranked_scenes:
                try:
                    red = self.reader.read(scene.assets["red"], bbox_wgs84=bbox)
                    nir = self.reader.read(scene.assets["nir"], bbox_wgs84=bbox)
                    features, valid = _spectral_features(red, nir)
                    result = suggest_boundaries(
                        features,
                        reference_location=reference_location,
                        resolution_m=abs(red.transform[0]),
                        estimated_area_ha=estimated_area_ha,
                        valid_mask=valid,
                    )
                except Exception:  # noqa: BLE001 - try the next intersecting STAC tile
                    continue
                return AcquiredBoundarySuggestion(
                    result=result,
                    scene_id=scene.id,
                    captured_at=scene.captured_at,
                    cloud_cover=scene.cloud_cover,
                    bbox=bbox,
                    provenance=provenance_payload(),
                )
            raise ValueError("No returned scene had valid pixels for the requested bounds.")
        except Exception:  # noqa: BLE001 - external catalog/COG failure must yield editable fallback
            fallback = suggest_boundaries(
                np.ones((4, 4), dtype=np.float64),
                reference_location=reference_location,
                resolution_m=10.0,
                estimated_area_ha=estimated_area_ha,
            )
            fallback = replace(fallback, reason="sentinel_imagery_temporarily_unavailable")
            return AcquiredBoundarySuggestion(
                result=fallback,
                scene_id=None,
                captured_at=None,
                cloud_cover=None,
                bbox=bbox,
                provenance=provenance_payload(),
            )


class EarthSearchBoundaryProvider:
    """Adapter from the geospatial engine to the stable PR2 API contract."""

    def __init__(self, service: SentinelBoundaryService | None = None) -> None:
        self.service = service or SentinelBoundaryService()

    def suggest(self, field: Field) -> BoundaryProposal:
        acquired = self.service.suggest(
            reference_location=field.reference_location.coordinates,
            estimated_area_ha=field.estimated_area_ha,
        )
        candidate = acquired.result.candidates[0]
        raw_coordinates = candidate.boundary["coordinates"]
        coordinates = tuple(tuple(tuple(position) for position in ring) for ring in raw_coordinates)
        boundary = GeoJSONPolygon(coordinates=coordinates)
        return BoundaryProposal(
            boundary=boundary,
            estimated_area_ha=candidate.estimated_area_ha,
            confidence=candidate.scores.total,
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
