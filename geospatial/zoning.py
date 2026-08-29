"""Deterministic temporal spectral zoning for Sentinel-2 field observations.

The engine deliberately describes *relative spectral development*.  It does not infer
agronomic causes.  Keeping this distinction in the lowest-level processing module makes it
harder for a presentation or an agent to accidentally turn a cluster into a diagnosis.

Arrays use the conventional ``(time, row, column)`` order.  Invalid observations are
represented by ``NaN`` and are never silently turned into healthy vegetation values.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

INDEX_NAMES = ("NDVI", "NDRE", "NDMI")
REQUIRED_BANDS = ("B04", "B05", "B08", "B11")
DEFAULT_INVALID_SCL_CLASSES = frozenset({0, 1, 3, 8, 9, 10, 11})


class InsufficientDataError(ValueError):
    """Raised when the observations cannot support at least two defensible zones."""


@dataclass(frozen=True, slots=True)
class PixelTransform:
    """Minimal north-up raster transform used to emit GeoJSON footprints.

    ``pixel_height`` may be negative, as it normally is in a north-up geospatial raster.
    The default transform emits pixel coordinates and is useful for deterministic tests.
    """

    origin_x: float = 0.0
    origin_y: float = 0.0
    pixel_width: float = 1.0
    pixel_height: float = -1.0
    crs: str | None = None

    def __post_init__(self) -> None:
        values = (self.origin_x, self.origin_y, self.pixel_width, self.pixel_height)
        if not all(isfinite(value) for value in values):
            raise ValueError("Pixel transform values must be finite.")
        if self.pixel_width == 0 or self.pixel_height == 0:
            raise ValueError("Pixel width and height cannot be zero.")


@dataclass(frozen=True, slots=True)
class TemporalFeatureMatrix:
    """Prepared features and their exact raster locations."""

    features: NDArray[np.float64]
    raw_features: NDArray[np.float64]
    pixel_indices: NDArray[np.int64]
    valid_pixel_mask: NDArray[np.bool_]
    observation_counts: NDArray[np.int64]
    feature_names: tuple[str, ...]
    time_count: int
    imputed_fraction: float


@dataclass(frozen=True, slots=True)
class CandidateScore:
    zone_count: int
    silhouette: float | None
    fragmentation: float
    adjusted_score: float | None
    smallest_zone_pixels: int
    accepted: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ZoneSelection:
    requested_zone_count: int | None
    selected_zone_count: int
    mode: str
    reason_pt: str
    reason_en: str
    candidates: tuple[CandidateScore, ...]


@dataclass(frozen=True, slots=True)
class TrajectoryPoint:
    scene_id: str
    time_index: int
    ndvi: float
    ndre: float
    ndmi: float
    valid_pixel_fraction: float


@dataclass(frozen=True, slots=True)
class ZoneStatistics:
    zone_id: str
    relative_label: str
    pixel_count: int
    area_ha: float
    area_percent: float
    mean_relative_signal: float
    trajectory: tuple[TrajectoryPoint, ...]
    geometry: dict[str, Any]
    summary_pt: str
    summary_en: str


@dataclass(frozen=True, slots=True)
class ZoningQuality:
    usable_pixel_count: int
    total_field_pixel_count: int
    usable_pixel_percent: float
    time_count: int
    imputed_feature_percent: float
    minimum_valid_observations: int


@dataclass(frozen=True, slots=True)
class ZoningResult:
    """Serializable zoning output with an auditable non-diagnostic scope."""

    selected_zone_count: int
    label_grid: tuple[tuple[int | None, ...], ...]
    zones: tuple[ZoneStatistics, ...]
    selection: ZoneSelection
    quality: ZoningQuality
    feature_collection: dict[str, Any]
    scope: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible built-ins only."""

        return asdict(self)


def safe_normalized_difference(
    high: ArrayLike,
    low: ArrayLike,
    *,
    valid_mask: ArrayLike | None = None,
    epsilon: float = 1e-12,
) -> NDArray[np.float64]:
    """Compute ``(high - low) / (high + low)`` without infinity or false zeroes."""

    high_array = np.asarray(high, dtype=np.float64)
    low_array = np.asarray(low, dtype=np.float64)
    if high_array.shape != low_array.shape:
        raise ValueError("Normalized-difference inputs must have identical shapes.")
    if epsilon <= 0:
        raise ValueError("epsilon must be positive.")

    denominator = high_array + low_array
    valid = np.isfinite(high_array) & np.isfinite(low_array)
    valid &= np.abs(denominator) > epsilon
    if valid_mask is not None:
        supplied_mask = np.asarray(valid_mask, dtype=bool)
        if supplied_mask.shape != high_array.shape:
            raise ValueError("valid_mask must match the band shape.")
        valid &= supplied_mask

    result = np.full(high_array.shape, np.nan, dtype=np.float64)
    np.divide(high_array - low_array, denominator, out=result, where=valid)
    # Reflectance noise can produce tiny excursions; clipping keeps the mathematical range.
    result[valid] = np.clip(result[valid], -1.0, 1.0)
    return result


def build_valid_observation_mask(
    bands: Mapping[str, ArrayLike],
    *,
    scl: ArrayLike | None = None,
    field_mask: ArrayLike | None = None,
    invalid_scl_classes: frozenset[int] = DEFAULT_INVALID_SCL_CLASSES,
) -> NDArray[np.bool_]:
    """Build a per-scene validity mask from reflectance, SCL and field coverage."""

    missing = [band for band in REQUIRED_BANDS if band not in bands]
    if missing:
        raise ValueError(f"Missing required bands: {', '.join(missing)}.")

    arrays = [np.asarray(bands[name], dtype=np.float64) for name in REQUIRED_BANDS]
    shape = arrays[0].shape
    if len(shape) != 3:
        raise ValueError("Band arrays must use (time, row, column) order.")
    if any(array.shape != shape for array in arrays[1:]):
        raise ValueError("All band arrays must have identical shapes.")

    valid = np.logical_and.reduce([np.isfinite(array) for array in arrays])
    # Negative surface reflectance and completely empty samples are not useful observations.
    valid &= np.logical_and.reduce([array >= 0 for array in arrays])
    valid &= np.logical_or.reduce([array > 0 for array in arrays])

    if scl is not None:
        scl_array = np.asarray(scl)
        if scl_array.shape != shape:
            raise ValueError("scl must match the band shape.")
        valid &= ~np.isin(scl_array, tuple(sorted(invalid_scl_classes)))

    if field_mask is not None:
        field = np.asarray(field_mask, dtype=bool)
        if field.shape != shape[1:]:
            raise ValueError("field_mask must have (row, column) shape.")
        valid &= field[np.newaxis, :, :]

    return valid


def compute_spectral_indices(
    bands: Mapping[str, ArrayLike],
    *,
    valid_mask: ArrayLike | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Compute NDVI, NDRE and NDMI from aligned Sentinel-2 reflectance arrays."""

    missing = [band for band in REQUIRED_BANDS if band not in bands]
    if missing:
        raise ValueError(f"Missing required bands: {', '.join(missing)}.")

    red = np.asarray(bands["B04"], dtype=np.float64)
    red_edge = np.asarray(bands["B05"], dtype=np.float64)
    nir = np.asarray(bands["B08"], dtype=np.float64)
    swir = np.asarray(bands["B11"], dtype=np.float64)
    if any(array.shape != red.shape for array in (red_edge, nir, swir)):
        raise ValueError("All band arrays must have identical shapes.")

    return {
        "NDVI": safe_normalized_difference(nir, red, valid_mask=valid_mask),
        "NDRE": safe_normalized_difference(nir, red_edge, valid_mask=valid_mask),
        "NDMI": safe_normalized_difference(nir, swir, valid_mask=valid_mask),
    }


def build_temporal_feature_matrix(
    indices: Mapping[str, ArrayLike],
    *,
    field_mask: ArrayLike | None = None,
    minimum_valid_observations: int = 2,
) -> TemporalFeatureMatrix:
    """Flatten valid pixels and robustly scale their three-index trajectories."""

    normalized = {
        name.upper(): np.asarray(value, dtype=np.float64) for name, value in indices.items()
    }
    missing = [name for name in INDEX_NAMES if name not in normalized]
    if missing:
        raise ValueError(f"Missing required indices: {', '.join(missing)}.")
    arrays = [normalized[name] for name in INDEX_NAMES]
    shape = arrays[0].shape
    if len(shape) != 3:
        raise ValueError("Index arrays must use (time, row, column) order.")
    if any(array.shape != shape for array in arrays[1:]):
        raise ValueError("All index arrays must have identical shapes.")

    time_count, height, width = shape
    if time_count < 2:
        raise InsufficientDataError("At least two source scenes are required.")
    if not 1 <= minimum_valid_observations <= time_count:
        raise ValueError("minimum_valid_observations must be between 1 and the scene count.")

    observation_valid = np.logical_and.reduce([np.isfinite(array) for array in arrays])
    observation_counts_grid = observation_valid.sum(axis=0)
    usable = observation_counts_grid >= minimum_valid_observations
    if field_mask is not None:
        field = np.asarray(field_mask, dtype=bool)
        if field.shape != (height, width):
            raise ValueError("field_mask must have (row, column) shape.")
        usable &= field

    pixel_indices = np.argwhere(usable).astype(np.int64)
    if pixel_indices.size == 0:
        raise InsufficientDataError("No pixels have enough valid temporal observations.")

    feature_names = tuple(
        f"{index_name.lower()}_t{time_index}"
        for time_index in range(time_count)
        for index_name in INDEX_NAMES
    )
    # Build time-major columns so every scene contributes NDVI, NDRE and NDMI together.
    stack = np.stack(arrays, axis=-1)  # time, row, column, index
    raw = stack[:, usable, :].transpose(1, 0, 2).reshape(len(pixel_indices), -1)
    missing_count = int(np.count_nonzero(~np.isfinite(raw)))

    imputed = raw.copy()
    for column in range(imputed.shape[1]):
        finite = np.isfinite(imputed[:, column])
        if not finite.any():
            scene_number = column // len(INDEX_NAMES) + 1
            raise InsufficientDataError(
                f"Scene {scene_number} has no valid pixels for one or more indices."
            )
        imputed[~finite, column] = float(np.median(imputed[finite, column]))

    median = np.median(imputed, axis=0)
    q25, q75 = np.percentile(imputed, (25, 75), axis=0)
    scale = q75 - q25
    standard_deviation = np.std(imputed, axis=0)
    scale = np.where(scale > 1e-9, scale, standard_deviation)
    scale = np.where(scale > 1e-9, scale, 1.0)
    features = (imputed - median) / scale

    return TemporalFeatureMatrix(
        features=features.astype(np.float64),
        raw_features=raw.astype(np.float64),
        pixel_indices=pixel_indices,
        valid_pixel_mask=usable,
        observation_counts=observation_counts_grid[usable].astype(np.int64),
        feature_names=feature_names,
        time_count=time_count,
        imputed_fraction=missing_count / raw.size,
    )


def analyze_temporal_zones(
    *,
    bands: Mapping[str, ArrayLike] | None = None,
    indices: Mapping[str, ArrayLike] | None = None,
    scl: ArrayLike | None = None,
    field_mask: ArrayLike | None = None,
    requested_zone_count: int | None = None,
    scene_ids: Sequence[str] | None = None,
    minimum_valid_observations: int = 2,
    minimum_pixels_per_zone: int = 8,
    pixel_area_m2: float = 100.0,
    transform: PixelTransform | None = None,
    spatial_weight: float = 0.35,
    random_state: int = 42,
) -> ZoningResult:
    """Run the complete non-diagnostic zoning pipeline.

    Either aligned Sentinel-2 ``bands`` or precomputed ``indices`` must be supplied.  A
    requested regrouping uses the same temporal matrix and therefore remains reproducible.
    """

    if (bands is None) == (indices is None):
        raise ValueError("Provide exactly one of bands or indices.")
    if minimum_pixels_per_zone < 2:
        raise ValueError("minimum_pixels_per_zone must be at least 2.")
    if not isfinite(pixel_area_m2) or pixel_area_m2 <= 0:
        raise ValueError("pixel_area_m2 must be a positive finite number.")
    if not isfinite(spatial_weight) or spatial_weight < 0:
        raise ValueError("spatial_weight must be a finite non-negative number.")

    supplied_field_mask = None if field_mask is None else np.asarray(field_mask, dtype=bool)
    if bands is not None:
        observation_mask = build_valid_observation_mask(
            bands,
            scl=scl,
            field_mask=supplied_field_mask,
        )
        computed_indices = compute_spectral_indices(bands, valid_mask=observation_mask)
    else:
        computed_indices = {
            name.upper(): np.asarray(value, dtype=np.float64)
            for name, value in (indices or {}).items()
        }

    matrix = build_temporal_feature_matrix(
        computed_indices,
        field_mask=supplied_field_mask,
        minimum_valid_observations=minimum_valid_observations,
    )
    labels, selection = _cluster_matrix(
        matrix,
        requested_zone_count=requested_zone_count,
        minimum_pixels_per_zone=minimum_pixels_per_zone,
        spatial_weight=spatial_weight,
        random_state=random_state,
    )

    label_map = np.full(matrix.valid_pixel_mask.shape, -1, dtype=np.int64)
    label_map[matrix.valid_pixel_mask] = labels
    # Use stable one-based zone IDs ordered from lower to higher aggregate signal.
    labels, label_map = _order_labels_by_signal(labels, label_map, matrix.raw_features)
    zone_count = selection.selected_zone_count
    identifiers = _validate_scene_ids(scene_ids, matrix.time_count)
    raster_transform = transform or PixelTransform()

    zones = _build_zone_statistics(
        matrix,
        labels,
        label_map,
        zone_count=zone_count,
        scene_ids=identifiers,
        pixel_area_m2=pixel_area_m2,
        transform=raster_transform,
    )
    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "zone_id": zone.zone_id,
                    "relative_label": zone.relative_label,
                    "area_ha": zone.area_ha,
                    "area_percent": zone.area_percent,
                },
                "geometry": zone.geometry,
            }
            for zone in zones
        ],
    }
    if raster_transform.crs:
        feature_collection["metadata"] = {"crs": raster_transform.crs}

    total_field_pixels = (
        int(np.count_nonzero(supplied_field_mask))
        if supplied_field_mask is not None
        else int(matrix.valid_pixel_mask.size)
    )
    usable_pixels = len(matrix.pixel_indices)
    quality = ZoningQuality(
        usable_pixel_count=usable_pixels,
        total_field_pixel_count=total_field_pixels,
        usable_pixel_percent=round(100.0 * usable_pixels / total_field_pixels, 3),
        time_count=matrix.time_count,
        imputed_feature_percent=round(100.0 * matrix.imputed_fraction, 3),
        minimum_valid_observations=minimum_valid_observations,
    )
    serializable_grid = tuple(
        tuple(None if value < 0 else int(value + 1) for value in row) for row in label_map
    )
    return ZoningResult(
        selected_zone_count=zone_count,
        label_grid=serializable_grid,
        zones=zones,
        selection=selection,
        quality=quality,
        feature_collection=feature_collection,
        scope={
            "kind": "relative_spatial_variability_only",
            "diagnostic": False,
            "excluded_inferences": ("pest", "disease", "soil", "water"),
            "disclaimer_pt": (
                "As zonas comparam sinais espectrais relativos e não identificam a causa "
                "das diferenças observadas."
            ),
            "disclaimer_en": (
                "The zones compare relative spectral signals and do not identify the cause "
                "of observed differences."
            ),
        },
    )


def _cluster_matrix(
    matrix: TemporalFeatureMatrix,
    *,
    requested_zone_count: int | None,
    minimum_pixels_per_zone: int,
    spatial_weight: float,
    random_state: int,
) -> tuple[NDArray[np.int64], ZoneSelection]:
    try:
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score
    except ImportError as exc:  # pragma: no cover - dependency failure is an install concern
        raise RuntimeError("Temporal zoning requires scikit-learn.") from exc

    pixel_count = len(matrix.pixel_indices)
    maximum_supported = min(7, pixel_count // minimum_pixels_per_zone)
    if maximum_supported < 2:
        required = minimum_pixels_per_zone * 2
        raise InsufficientDataError(
            f"At least {required} usable pixels are required to support two zones."
        )
    if requested_zone_count is not None and not 2 <= requested_zone_count <= 7:
        raise ValueError("requested_zone_count must be between 2 and 7.")

    coordinates = matrix.pixel_indices.astype(np.float64)
    coordinate_mean = coordinates.mean(axis=0)
    coordinate_scale = np.ptp(coordinates, axis=0)
    coordinate_scale = np.where(coordinate_scale > 0, coordinate_scale, 1.0)
    normalized_coordinates = (coordinates - coordinate_mean) / coordinate_scale
    weighted_coordinates = (
        normalized_coordinates * np.sqrt(matrix.features.shape[1]) * spatial_weight
    )
    clustering_features = np.column_stack((matrix.features, weighted_coordinates))

    requested_was_capped = (
        requested_zone_count is not None and requested_zone_count > maximum_supported
    )
    if requested_zone_count is None:
        candidates_to_run = range(2, maximum_supported + 1)
    elif requested_was_capped:
        candidates_to_run = range(maximum_supported, 1, -1)
    else:
        candidates_to_run = (requested_zone_count,)

    candidates: list[CandidateScore] = []
    accepted_results: dict[int, tuple[NDArray[np.int64], float]] = {}
    for zone_count in candidates_to_run:
        estimator = KMeans(n_clusters=zone_count, random_state=random_state, n_init=20)
        labels = estimator.fit_predict(clustering_features).astype(np.int64)
        labels = _smooth_small_components(
            labels,
            matrix.pixel_indices,
            matrix.valid_pixel_mask.shape,
            minimum_component_pixels=max(2, minimum_pixels_per_zone // 2),
        )
        counts = np.bincount(labels, minlength=zone_count)
        smallest = int(counts.min()) if len(counts) == zone_count else 0
        if len(np.unique(labels)) != zone_count or smallest < minimum_pixels_per_zone:
            candidates.append(
                CandidateScore(
                    zone_count=zone_count,
                    silhouette=None,
                    fragmentation=_fragmentation(labels, matrix.pixel_indices),
                    adjusted_score=None,
                    smallest_zone_pixels=smallest,
                    accepted=False,
                    reason="zone_below_minimum_pixel_support",
                )
            )
            continue

        sample_size = min(2_000, len(clustering_features))
        silhouette = float(
            silhouette_score(
                clustering_features,
                labels,
                sample_size=sample_size if sample_size < len(labels) else None,
                random_state=random_state,
            )
        )
        fragmentation = _fragmentation(labels, matrix.pixel_indices)
        # A modest penalty resists over-segmentation and scattered cartographic noise.
        adjusted = silhouette - 0.015 * (zone_count - 2) - 0.025 * fragmentation
        candidates.append(
            CandidateScore(
                zone_count=zone_count,
                silhouette=round(silhouette, 6),
                fragmentation=round(fragmentation, 6),
                adjusted_score=round(adjusted, 6),
                smallest_zone_pixels=smallest,
                accepted=True,
                reason="accepted",
            )
        )
        accepted_results[zone_count] = (labels, adjusted)

    if not accepted_results:
        raise InsufficientDataError(
            "Usable pixels do not form two zones with the minimum spatial support."
        )

    if requested_zone_count is not None and requested_zone_count in accepted_results:
        selected = requested_zone_count
        mode = "requested"
        reason_pt = f"Reagrupamento executado nas {selected} zonas solicitadas."
        reason_en = f"Regrouping used the requested {selected} zones."
    elif requested_zone_count is not None:
        selected = max(accepted_results)
        mode = "requested_capped"
        reason_pt = (
            f"A solicitação de {requested_zone_count} zonas foi limitada a {selected}, "
            "pois mais zonas não teriam suporte mínimo de pixels."
        )
        reason_en = (
            f"The request for {requested_zone_count} zones was capped at {selected} because "
            "additional zones lacked minimum pixel support."
        )
    else:
        selected = max(accepted_results, key=lambda count: (accepted_results[count][1], -count))
        mode = "automatic"
        reason_pt = (
            f"Foram selecionadas {selected} zonas pelo melhor equilíbrio entre separação "
            "espectral, continuidade espacial e simplicidade."
        )
        reason_en = (
            f"{selected} zones were selected for the best balance of spectral separation, "
            "spatial continuity and simplicity."
        )

    selection = ZoneSelection(
        requested_zone_count=requested_zone_count,
        selected_zone_count=selected,
        mode=mode,
        reason_pt=reason_pt,
        reason_en=reason_en,
        candidates=tuple(sorted(candidates, key=lambda candidate: candidate.zone_count)),
    )
    return accepted_results[selected][0], selection


def _smooth_small_components(
    labels: NDArray[np.int64],
    pixel_indices: NDArray[np.int64],
    raster_shape: tuple[int, int],
    *,
    minimum_component_pixels: int,
) -> NDArray[np.int64]:
    """Reassign tiny islands to their most common adjacent zone."""

    grid = np.full(raster_shape, -1, dtype=np.int64)
    grid[pixel_indices[:, 0], pixel_indices[:, 1]] = labels
    for _ in range(2):
        changes: list[tuple[list[tuple[int, int]], int]] = []
        for label in sorted(int(value) for value in np.unique(labels)):
            for component in _components_for_label(grid, label):
                if len(component) >= minimum_component_pixels:
                    continue
                neighbours: Counter[int] = Counter()
                for row, column in component:
                    for next_row, next_column in _neighbours(row, column, raster_shape):
                        neighbour = int(grid[next_row, next_column])
                        if neighbour >= 0 and neighbour != label:
                            neighbours[neighbour] += 1
                if neighbours:
                    replacement = min(
                        neighbours,
                        key=lambda value: (-neighbours[value], value),
                    )
                    changes.append((component, replacement))
        if not changes:
            break
        for component, replacement in changes:
            for row, column in component:
                grid[row, column] = replacement

    return grid[pixel_indices[:, 0], pixel_indices[:, 1]].astype(np.int64)


def _components_for_label(grid: NDArray[np.int64], label: int) -> list[list[tuple[int, int]]]:
    seen: set[tuple[int, int]] = set()
    components: list[list[tuple[int, int]]] = []
    height, width = grid.shape
    for row, column in np.argwhere(grid == label):
        start = (int(row), int(column))
        if start in seen:
            continue
        queue = deque([start])
        seen.add(start)
        component: list[tuple[int, int]] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbour in _neighbours(*current, (height, width)):
                if neighbour not in seen and grid[neighbour] == label:
                    seen.add(neighbour)
                    queue.append(neighbour)
        components.append(component)
    return components


def _neighbours(row: int, column: int, shape: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    height, width = shape
    return tuple(
        (next_row, next_column)
        for next_row, next_column in (
            (row - 1, column),
            (row + 1, column),
            (row, column - 1),
            (row, column + 1),
        )
        if 0 <= next_row < height and 0 <= next_column < width
    )


def _fragmentation(labels: NDArray[np.int64], pixel_indices: NDArray[np.int64]) -> float:
    height = int(pixel_indices[:, 0].max()) + 1
    width = int(pixel_indices[:, 1].max()) + 1
    grid = np.full((height, width), -1, dtype=np.int64)
    grid[pixel_indices[:, 0], pixel_indices[:, 1]] = labels
    zone_count = len(np.unique(labels))
    component_count = sum(
        len(_components_for_label(grid, int(label))) for label in np.unique(labels)
    )
    return max(0.0, (component_count - zone_count) / max(1, len(labels)))


def _order_labels_by_signal(
    labels: NDArray[np.int64],
    label_map: NDArray[np.int64],
    raw_features: NDArray[np.float64],
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    unique = np.unique(labels)
    mean_by_label = {
        int(label): float(np.nanmean(raw_features[labels == label])) for label in unique
    }
    ordered = sorted(
        (int(label) for label in unique),
        key=lambda label: (mean_by_label[label], label),
    )
    remapping = {old: new for new, old in enumerate(ordered)}
    ordered_labels = np.asarray([remapping[int(label)] for label in labels], dtype=np.int64)
    ordered_map = label_map.copy()
    for old, new in remapping.items():
        ordered_map[label_map == old] = new
    return ordered_labels, ordered_map


def _build_zone_statistics(
    matrix: TemporalFeatureMatrix,
    labels: NDArray[np.int64],
    label_map: NDArray[np.int64],
    *,
    zone_count: int,
    scene_ids: tuple[str, ...],
    pixel_area_m2: float,
    transform: PixelTransform,
) -> tuple[ZoneStatistics, ...]:
    total_pixels = len(labels)
    aggregate_signal = np.nanmean(matrix.raw_features, axis=1)
    field_mean = float(np.nanmean(aggregate_signal))
    field_std = float(np.nanstd(aggregate_signal))
    threshold = max(0.02, field_std * 0.25)
    zones: list[ZoneStatistics] = []

    for label in range(zone_count):
        selected = labels == label
        pixel_count = int(np.count_nonzero(selected))
        zone_signal = float(np.nanmean(aggregate_signal[selected]))
        if zone_signal < field_mean - threshold:
            relative_label = "lower_than_field"
            summary_pt = (
                "A trajetória espectral desta zona ficou relativamente abaixo do conjunto "
                "do talhão."
            )
            summary_en = (
                "This zone's spectral trajectory was relatively below the field as a whole."
            )
        elif zone_signal > field_mean + threshold:
            relative_label = "higher_than_field"
            summary_pt = (
                "A trajetória espectral desta zona ficou relativamente acima do conjunto do talhão."
            )
            summary_en = (
                "This zone's spectral trajectory was relatively above the field as a whole."
            )
        else:
            relative_label = "similar_to_field"
            summary_pt = "A trajetória espectral desta zona ficou próxima do conjunto do talhão."
            summary_en = "This zone's spectral trajectory remained close to the field as a whole."

        trajectory: list[TrajectoryPoint] = []
        reshaped = matrix.raw_features[selected].reshape(pixel_count, matrix.time_count, 3)
        for time_index, scene_id in enumerate(scene_ids):
            scene_values = reshaped[:, time_index, :]
            valid_rows = np.all(np.isfinite(scene_values), axis=1)
            valid_fraction = float(np.count_nonzero(valid_rows) / pixel_count)
            means = np.nanmean(scene_values, axis=0)
            trajectory.append(
                TrajectoryPoint(
                    scene_id=scene_id,
                    time_index=time_index,
                    ndvi=round(float(means[0]), 6),
                    ndre=round(float(means[1]), 6),
                    ndmi=round(float(means[2]), 6),
                    valid_pixel_fraction=round(valid_fraction, 6),
                )
            )

        geometry = _label_geometry(label_map, label, transform)
        zones.append(
            ZoneStatistics(
                zone_id=f"zone-{label + 1}",
                relative_label=relative_label,
                pixel_count=pixel_count,
                area_ha=round(pixel_count * pixel_area_m2 / 10_000.0, 6),
                area_percent=round(pixel_count * 100.0 / total_pixels, 6),
                mean_relative_signal=round(zone_signal, 6),
                trajectory=tuple(trajectory),
                geometry=geometry,
                summary_pt=summary_pt,
                summary_en=summary_en,
            )
        )
    return tuple(zones)


def _label_geometry(
    label_map: NDArray[np.int64], label: int, transform: PixelTransform
) -> dict[str, Any]:
    """Emit an exact raster footprint as compact horizontal-run polygons."""

    polygons: list[list[list[list[float]]]] = []
    for row in range(label_map.shape[0]):
        columns = np.flatnonzero(label_map[row] == label)
        if not len(columns):
            continue
        run_start = int(columns[0])
        run_end = run_start
        for column_value in columns[1:]:
            column = int(column_value)
            if column == run_end + 1:
                run_end = column
                continue
            polygons.append([_pixel_run_ring(row, run_start, run_end, transform)])
            run_start = run_end = column
        polygons.append([_pixel_run_ring(row, run_start, run_end, transform)])

    if not polygons:
        raise RuntimeError("A selected zone has no footprint pixels.")
    return {"type": "MultiPolygon", "coordinates": polygons}


def _pixel_run_ring(
    row: int,
    start_column: int,
    end_column: int,
    transform: PixelTransform,
) -> list[list[float]]:
    x_a = transform.origin_x + start_column * transform.pixel_width
    x_b = transform.origin_x + (end_column + 1) * transform.pixel_width
    y_a = transform.origin_y + row * transform.pixel_height
    y_b = transform.origin_y + (row + 1) * transform.pixel_height
    left, right = sorted((x_a, x_b))
    bottom, top = sorted((y_a, y_b))
    return [
        [float(left), float(bottom)],
        [float(right), float(bottom)],
        [float(right), float(top)],
        [float(left), float(top)],
        [float(left), float(bottom)],
    ]


def _validate_scene_ids(scene_ids: Sequence[str] | None, time_count: int) -> tuple[str, ...]:
    if scene_ids is None:
        return tuple(f"scene-{index + 1}" for index in range(time_count))
    identifiers = tuple(str(identifier).strip() for identifier in scene_ids)
    if len(identifiers) != time_count:
        raise ValueError("scene_ids must match the temporal scene count.")
    if any(not identifier for identifier in identifiers):
        raise ValueError("scene_ids cannot be blank.")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("scene_ids cannot contain duplicates.")
    return identifiers
