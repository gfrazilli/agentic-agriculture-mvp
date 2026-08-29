"""Deterministic, review-first crop boundary suggestions.

The engine deliberately answers only *where a spectrally coherent region may be*.
It does not attach agronomic meaning to the signal and every result remains editable
and subject to farmer confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import cos, exp, isfinite, log, pi, radians, sqrt

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
Pixel = tuple[int, int]
LonLat = tuple[float, float]


@dataclass(frozen=True, slots=True)
class BoundaryCandidateScores:
    """Normalised, independently inspectable parts of a candidate score."""

    proximity: float
    estimated_area: float
    spectral_homogeneity: float
    edge_strength: float
    total: float

    def as_dict(self) -> dict[str, float]:
        return {
            "proximity": self.proximity,
            "estimated_area": self.estimated_area,
            "spectral_homogeneity": self.spectral_homogeneity,
            "edge_strength": self.edge_strength,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class BoundaryCandidate:
    """A possible field outline, never an automatically confirmed boundary."""

    rank: int
    boundary: dict[str, object]
    estimated_area_ha: float
    scores: BoundaryCandidateScores
    source: str
    rationale: tuple[str, ...]
    editable: bool = True
    requires_confirmation: bool = True


@dataclass(frozen=True, slots=True)
class BoundarySuggestionResult:
    candidates: tuple[BoundaryCandidate, ...]
    used_fallback: bool
    reason: str | None = None


def suggest_boundaries(
    spectral_grid: NDArray[np.floating] | NDArray[np.integer],
    *,
    reference_location: LonLat,
    resolution_m: float,
    estimated_area_ha: float | None = None,
    valid_mask: NDArray[np.bool_] | None = None,
    reference_pixel: Pixel | None = None,
    max_candidates: int = 3,
) -> BoundarySuggestionResult:
    """Return up to three connected, spectrally coherent boundary candidates.

    ``spectral_grid`` may be an index image shaped ``(height, width)`` or a
    multiband composition shaped ``(height, width, bands)``. The reference
    location is treated as the geographic centre of that grid. Region growing
    starts at ``reference_pixel`` (the grid centre by default), so candidates
    are connected and deterministic even when spectral values tie.

    Scores are evidence for *boundary selection*, not evidence of crop health or
    of any causal diagnosis.
    """

    features = _validate_and_shape_grid(spectral_grid)
    height, width, _ = features.shape
    longitude, latitude = _validate_location(reference_location)
    resolution = _positive_number(resolution_m, "resolution_m")
    requested_area = (
        None
        if estimated_area_ha is None
        else _positive_number(estimated_area_ha, "estimated_area_ha")
    )
    if not 1 <= max_candidates <= 3:
        raise ValueError("max_candidates must be between 1 and 3.")

    finite_mask = np.all(np.isfinite(features), axis=2)
    if valid_mask is None:
        usable = finite_mask
    else:
        supplied_mask = np.asarray(valid_mask, dtype=bool)
        if supplied_mask.shape != (height, width):
            raise ValueError("valid_mask must match the first two grid dimensions.")
        usable = supplied_mask & finite_mask

    seed = _select_seed(usable, reference_pixel)
    if seed is None or int(usable.sum()) < 9:
        return _fallback_result(
            reference_location=(longitude, latitude),
            resolution_m=resolution,
            estimated_area_ha=requested_area,
            reason="insufficient_valid_or_variable_spectral_data",
        )
    normalised, informative = _normalise_features(features, usable)
    if not informative:
        reason = "insufficient_valid_or_variable_spectral_data"
        return _fallback_result(
            reference_location=(longitude, latitude),
            resolution_m=resolution,
            estimated_area_ha=requested_area,
            reason=reason,
        )

    seed_values = normalised[seed[0], seed[1], :]
    spectral_distance = np.sqrt(np.mean(np.square(normalised - seed_values), axis=2))
    rows, columns = np.indices((height, width))
    spatial_distance = np.hypot(rows - seed[0], columns - seed[1])
    spatial_scale = max(float(np.hypot(height, width)), 1.0)
    growth_cost = spectral_distance + 0.03 * spatial_distance / spatial_scale
    growth_order = _region_growth_order(growth_cost, usable, seed)
    if len(growth_order) < 4:
        return _fallback_result(
            reference_location=(longitude, latitude),
            resolution_m=resolution,
            estimated_area_ha=requested_area,
            reason="reference_region_too_small",
        )

    target_sizes = _candidate_sizes(
        connected_pixels=len(growth_order),
        valid_pixels=int(usable.sum()),
        resolution_m=resolution,
        estimated_area_ha=requested_area,
        max_candidates=max_candidates,
    )
    candidates: list[BoundaryCandidate] = []
    for size in target_sizes:
        mask = np.zeros((height, width), dtype=bool)
        selected = growth_order[:size]
        mask[tuple(zip(*selected, strict=True))] = True
        area_ha = size * resolution * resolution / 10_000.0
        scores = _score_candidate(
            mask=mask,
            normalised=normalised,
            usable=usable,
            seed=seed,
            resolution_m=resolution,
            area_ha=area_ha,
            estimated_area_ha=requested_area,
        )
        candidates.append(
            BoundaryCandidate(
                rank=0,
                boundary=_mask_to_geojson(
                    mask,
                    reference_location=(longitude, latitude),
                    resolution_m=resolution,
                ),
                estimated_area_ha=round(area_ha, 6),
                scores=scores,
                source="spectral-region-growth",
                rationale=_rationale(scores, area_ha, requested_area),
            )
        )

    ordered = sorted(
        candidates,
        key=lambda candidate: (-candidate.scores.total, candidate.estimated_area_ha),
    )
    ranked = tuple(
        BoundaryCandidate(
            rank=rank,
            boundary=candidate.boundary,
            estimated_area_ha=candidate.estimated_area_ha,
            scores=candidate.scores,
            source=candidate.source,
            rationale=candidate.rationale,
        )
        for rank, candidate in enumerate(ordered, start=1)
    )
    return BoundarySuggestionResult(candidates=ranked, used_fallback=False)


def _validate_and_shape_grid(
    spectral_grid: NDArray[np.floating] | NDArray[np.integer],
) -> FloatArray:
    grid = np.asarray(spectral_grid, dtype=np.float64)
    if grid.ndim == 2:
        grid = grid[:, :, np.newaxis]
    if grid.ndim != 3 or grid.shape[2] < 1:
        raise ValueError("spectral_grid must have shape (height, width[, bands]).")
    if grid.shape[0] < 2 or grid.shape[1] < 2:
        raise ValueError("spectral_grid must contain at least a 2 x 2 grid.")
    return grid


def _validate_location(reference_location: LonLat) -> LonLat:
    if len(reference_location) != 2:
        raise ValueError("reference_location must contain longitude and latitude.")
    longitude, latitude = (float(value) for value in reference_location)
    if not isfinite(longitude) or not -180.0 <= longitude <= 180.0:
        raise ValueError("reference longitude must be between -180 and 180.")
    if not isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise ValueError("reference latitude must be between -90 and 90.")
    return longitude, latitude


def _positive_number(value: float, name: str) -> float:
    converted = float(value)
    if not isfinite(converted) or converted <= 0:
        raise ValueError(f"{name} must be a finite positive number.")
    return converted


def _select_seed(usable: BoolArray, reference_pixel: Pixel | None) -> Pixel | None:
    height, width = usable.shape
    if reference_pixel is None:
        requested = ((height - 1) // 2, (width - 1) // 2)
    else:
        if len(reference_pixel) != 2:
            raise ValueError("reference_pixel must contain row and column.")
        requested = (int(reference_pixel[0]), int(reference_pixel[1]))
        if not (0 <= requested[0] < height and 0 <= requested[1] < width):
            raise ValueError("reference_pixel must be inside spectral_grid.")
    if usable[requested]:
        return requested
    valid_pixels = np.argwhere(usable)
    if valid_pixels.size == 0:
        return None
    distances = np.square(valid_pixels[:, 0] - requested[0]) + np.square(
        valid_pixels[:, 1] - requested[1]
    )
    row, column = valid_pixels[int(np.argmin(distances))]
    return int(row), int(column)


def _normalise_features(features: FloatArray, usable: BoolArray) -> tuple[FloatArray, bool]:
    usable_values = features[usable]
    medians = np.median(usable_values, axis=0)
    lower, upper = np.percentile(usable_values, (10.0, 90.0), axis=0)
    scales = upper - lower
    standard_deviations = np.std(usable_values, axis=0)
    scales = np.where(scales > 1e-9, scales, standard_deviations)
    informative_channels = scales > 1e-9
    safe_scales = np.where(informative_channels, scales, 1.0)
    normalised = (features - medians) / safe_scales
    normalised[:, :, ~informative_channels] = 0.0
    return normalised, bool(np.any(informative_channels))


def _region_growth_order(cost: FloatArray, usable: BoolArray, seed: Pixel) -> list[Pixel]:
    height, width = usable.shape
    queue: list[tuple[float, int, int]] = []
    heappush(queue, (float(cost[seed]), seed[0], seed[1]))
    queued = {seed}
    selected: list[Pixel] = []
    while queue:
        _, row, column = heappop(queue)
        selected.append((row, column))
        for next_row, next_column in (
            (row - 1, column),
            (row, column - 1),
            (row, column + 1),
            (row + 1, column),
        ):
            neighbour = (next_row, next_column)
            if (
                0 <= next_row < height
                and 0 <= next_column < width
                and usable[neighbour]
                and neighbour not in queued
            ):
                queued.add(neighbour)
                heappush(queue, (float(cost[neighbour]), next_row, next_column))
    return selected


def _candidate_sizes(
    *,
    connected_pixels: int,
    valid_pixels: int,
    resolution_m: float,
    estimated_area_ha: float | None,
    max_candidates: int,
) -> tuple[int, ...]:
    if estimated_area_ha is None:
        target = max(4, round(valid_pixels * 0.25))
    else:
        target = max(4, round(estimated_area_ha * 10_000.0 / resolution_m**2))
    target = min(target, connected_pixels)
    factors = {
        1: (1.0,),
        2: (0.82, 1.18),
        3: (0.72, 1.0, 1.32),
    }[max_candidates]
    sizes = {min(connected_pixels, max(4, round(target * factor))) for factor in factors}
    return tuple(sorted(sizes))


def _score_candidate(
    *,
    mask: BoolArray,
    normalised: FloatArray,
    usable: BoolArray,
    seed: Pixel,
    resolution_m: float,
    area_ha: float,
    estimated_area_ha: float | None,
) -> BoundaryCandidateScores:
    selected_pixels = np.argwhere(mask)
    centroid = np.mean(selected_pixels, axis=0)
    distance_m = float(np.hypot(*(centroid - np.asarray(seed)))) * resolution_m
    equivalent_radius_m = max(sqrt(area_ha * 10_000.0 / pi), resolution_m)
    proximity = exp(-distance_m / equivalent_radius_m)

    if estimated_area_ha is None:
        area_score = 1.0
    else:
        area_score = exp(-abs(log(area_ha / estimated_area_ha)))

    selected_values = normalised[mask]
    channel_variance = np.var(selected_values, axis=0)
    homogeneity = exp(-sqrt(float(np.mean(channel_variance))))
    edge = _edge_strength(mask, normalised, usable)
    total = 0.30 * proximity + 0.30 * area_score + 0.20 * homogeneity + 0.20 * edge
    return BoundaryCandidateScores(
        proximity=_unit(proximity),
        estimated_area=_unit(area_score),
        spectral_homogeneity=_unit(homogeneity),
        edge_strength=_unit(edge),
        total=_unit(total),
    )


def _edge_strength(mask: BoolArray, features: FloatArray, usable: BoolArray) -> float:
    horizontal = np.linalg.norm(features[:, 1:, :] - features[:, :-1, :], axis=2)
    vertical = np.linalg.norm(features[1:, :, :] - features[:-1, :, :], axis=2)
    horizontal_valid = usable[:, 1:] & usable[:, :-1]
    vertical_valid = usable[1:, :] & usable[:-1, :]
    all_edges = np.concatenate(
        (horizontal[horizontal_valid], vertical[vertical_valid]),
    )
    if all_edges.size == 0:
        return 0.0
    # Most neighbouring pixels in a quiet scene can be identical. Deriving the
    # normaliser from positive contrasts keeps a narrow but real field edge from
    # disappearing merely because it occupies less than 10% of the image.
    positive_edges = all_edges[all_edges > 1e-12]
    if positive_edges.size == 0:
        return 0.0
    scale = float(np.percentile(positive_edges, 90.0))
    if scale <= 1e-12:
        return 0.0

    horizontal_boundary = horizontal_valid & (mask[:, 1:] != mask[:, :-1])
    vertical_boundary = vertical_valid & (mask[1:, :] != mask[:-1, :])
    boundary_edges = np.concatenate(
        (horizontal[horizontal_boundary], vertical[vertical_boundary]),
    )
    if boundary_edges.size == 0:
        return 0.0
    return float(np.clip(np.mean(boundary_edges) / scale, 0.0, 1.0))


def _unit(value: float) -> float:
    return round(float(np.clip(value, 0.0, 1.0)), 6)


def _rationale(
    scores: BoundaryCandidateScores,
    area_ha: float,
    requested_area_ha: float | None,
) -> tuple[str, ...]:
    area_context = (
        f"area observada {area_ha:.3f} ha; sem estimativa informada"
        if requested_area_ha is None
        else f"area observada {area_ha:.3f} ha; estimativa informada {requested_area_ha:.3f} ha"
    )
    return (
        area_context,
        f"proximidade ao ponto de referencia: {scores.proximity:.3f}",
        f"homogeneidade espectral interna: {scores.spectral_homogeneity:.3f}",
        f"forca relativa da borda: {scores.edge_strength:.3f}",
        "o resultado delimita um padrao espectral e nao diagnostica causa agronomica",
    )


def _mask_to_geojson(
    mask: BoolArray,
    *,
    reference_location: LonLat,
    resolution_m: float,
) -> dict[str, object]:
    height, width = mask.shape
    corner_points: set[tuple[float, float]] = set()
    for row, column in np.argwhere(mask):
        for row_corner, column_corner in (
            (row, column),
            (row, column + 1),
            (row + 1, column),
            (row + 1, column + 1),
        ):
            east_m = (float(column_corner) - width / 2.0) * resolution_m
            north_m = (height / 2.0 - float(row_corner)) * resolution_m
            corner_points.add((east_m, north_m))
    hull = _convex_hull(corner_points)
    if len(hull) > 96:
        step = len(hull) / 96.0
        hull = [hull[int(index * step)] for index in range(96)]
    ring = [_metres_to_lon_lat(point, reference_location=reference_location) for point in hull]
    ring.append(ring[0])
    return {
        "type": "Polygon",
        "coordinates": [[list(coordinate) for coordinate in ring]],
    }


def _convex_hull(points: set[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted(points)
    if len(ordered) < 3:
        raise ValueError("A boundary candidate needs at least three distinct corners.")

    def cross(
        origin: tuple[float, float],
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
            second[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _metres_to_lon_lat(point: tuple[float, float], *, reference_location: LonLat) -> LonLat:
    east_m, north_m = point
    longitude, latitude = reference_location
    metres_per_degree_longitude = max(111_320.0 * abs(cos(radians(latitude))), 1_000.0)
    return (
        round(longitude + east_m / metres_per_degree_longitude, 9),
        round(latitude + north_m / 110_574.0, 9),
    )


def _fallback_result(
    *,
    reference_location: LonLat,
    resolution_m: float,
    estimated_area_ha: float | None,
    reason: str,
) -> BoundarySuggestionResult:
    area_ha = estimated_area_ha or max(9.0 * resolution_m**2 / 10_000.0, 0.01)
    side_m = sqrt(area_ha * 10_000.0)
    half_side = side_m / 2.0
    corners_m = (
        (-half_side, -half_side),
        (half_side, -half_side),
        (half_side, half_side),
        (-half_side, half_side),
        (-half_side, -half_side),
    )
    ring = [
        list(_metres_to_lon_lat(point, reference_location=reference_location))
        for point in corners_m
    ]
    scores = BoundaryCandidateScores(
        proximity=1.0,
        estimated_area=1.0 if estimated_area_ha is not None else 0.5,
        spectral_homogeneity=0.0,
        edge_strength=0.0,
        total=0.6 if estimated_area_ha is not None else 0.45,
    )
    candidate = BoundaryCandidate(
        rank=1,
        boundary={"type": "Polygon", "coordinates": [ring]},
        estimated_area_ha=round(area_ha, 6),
        scores=scores,
        source="geometric-fallback",
        rationale=(
            "dados espectrais insuficientes para diferenciar candidatos",
            "poligono inicial geometrico baseado no ponto e na area estimada",
            "o poligono deve ser editado e confirmado pelo agricultor",
        ),
    )
    return BoundarySuggestionResult(
        candidates=(candidate,),
        used_fallback=True,
        reason=reason,
    )
