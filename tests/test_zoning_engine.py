from __future__ import annotations

import json

import numpy as np
import pytest

from geospatial.zoning import (
    InsufficientDataError,
    PixelTransform,
    analyze_temporal_zones,
    build_temporal_feature_matrix,
    build_valid_observation_mask,
    compute_spectral_indices,
    safe_normalized_difference,
)


def _two_zone_indices(*, height: int = 12, width: int = 16) -> dict[str, np.ndarray]:
    time_count = 3
    indices = {
        "NDVI": np.empty((time_count, height, width), dtype=float),
        "NDRE": np.empty((time_count, height, width), dtype=float),
        "NDMI": np.empty((time_count, height, width), dtype=float),
    }
    lower = ((0.18, 0.08, 0.03), (0.31, 0.14, 0.07), (0.46, 0.22, 0.12))
    higher = ((0.38, 0.20, 0.11), (0.60, 0.34, 0.19), (0.78, 0.49, 0.28))
    for time_index in range(time_count):
        for index_position, name in enumerate(("NDVI", "NDRE", "NDMI")):
            indices[name][time_index, :, : width // 2] = lower[time_index][index_position]
            indices[name][time_index, :, width // 2 :] = higher[time_index][index_position]
    # Small deterministic gradients avoid duplicate-point warnings without erasing the zones.
    row_gradient = np.linspace(-0.002, 0.002, height)[:, np.newaxis]
    for values in indices.values():
        values += row_gradient
    return indices


def _gradient_indices(height: int = 18, width: int = 21) -> dict[str, np.ndarray]:
    rows, columns = np.indices((height, width))
    spatial = columns / (width - 1) + 0.15 * rows / (height - 1)
    return {
        "NDVI": np.stack([0.20 + 0.10 * time + 0.35 * spatial for time in range(3)]),
        "NDRE": np.stack([0.08 + 0.06 * time + 0.20 * spatial for time in range(3)]),
        "NDMI": np.stack([0.03 + 0.04 * time + 0.12 * spatial for time in range(3)]),
    }


def test_safe_indices_honor_zero_denominator_and_cloud_mask() -> None:
    high = np.array([[[0.8, 0.0, 0.7]]])
    low = np.array([[[0.2, 0.0, 0.3]]])
    valid = np.array([[[True, True, False]]])
    result = safe_normalized_difference(high, low, valid_mask=valid)

    assert result[0, 0, 0] == pytest.approx(0.6)
    assert np.isnan(result[0, 0, 1])
    assert np.isnan(result[0, 0, 2])

    bands = {
        "B04": np.full((2, 2, 2), 0.2),
        "B05": np.full((2, 2, 2), 0.3),
        "B08": np.full((2, 2, 2), 0.8),
        "B11": np.full((2, 2, 2), 0.4),
    }
    scl = np.full((2, 2, 2), 4)
    scl[:, 0, 1] = 9
    mask = build_valid_observation_mask(bands, scl=scl)
    computed = compute_spectral_indices(bands, valid_mask=mask)

    assert mask[:, 0, 1].tolist() == [False, False]
    assert np.isnan(computed["NDVI"][:, 0, 1]).all()
    assert computed["NDVI"][:, 1, 1] == pytest.approx([0.6, 0.6])
    assert computed["NDRE"][:, 1, 1] == pytest.approx([5 / 11, 5 / 11])
    assert computed["NDMI"][:, 1, 1] == pytest.approx([1 / 3, 1 / 3])


def test_temporal_matrix_imputes_only_supported_pixels() -> None:
    indices = _two_zone_indices(height=4, width=6)
    indices["NDVI"][1, 1, 1] = np.nan
    indices["NDRE"][1, 1, 1] = np.nan
    indices["NDMI"][1, 1, 1] = np.nan
    for values in indices.values():
        values[:, 3, 5] = np.nan

    matrix = build_temporal_feature_matrix(indices, minimum_valid_observations=2)

    assert matrix.time_count == 3
    assert matrix.features.shape == (23, 9)
    assert np.isfinite(matrix.features).all()
    assert matrix.imputed_fraction == pytest.approx(3 / (23 * 9))
    assert not matrix.valid_pixel_mask[3, 5]


def test_requested_two_zones_recover_spatially_coherent_relative_patterns() -> None:
    result = analyze_temporal_zones(
        indices=_two_zone_indices(),
        requested_zone_count=2,
        scene_ids=("early", "middle", "late"),
        pixel_area_m2=100,
        transform=PixelTransform(
            origin_x=-48.91,
            origin_y=-23.97,
            pixel_width=0.0001,
            pixel_height=-0.0001,
            crs="EPSG:4326",
        ),
    )

    labels = np.asarray(result.label_grid, dtype=int)
    assert result.selected_zone_count == 2
    assert result.selection.mode == "requested"
    assert np.mean(labels[:, :8] == 1) > 0.95
    assert np.mean(labels[:, 8:] == 2) > 0.95
    assert [zone.relative_label for zone in result.zones] == [
        "lower_than_field",
        "higher_than_field",
    ]
    assert sum(zone.area_percent for zone in result.zones) == pytest.approx(100.0)
    assert all(len(zone.trajectory) == 3 for zone in result.zones)
    assert result.feature_collection["metadata"]["crs"] == "EPSG:4326"
    assert json.loads(json.dumps(result.to_dict()))["selected_zone_count"] == 2


def test_regrouping_honors_requested_count_and_enforces_seven_zone_ceiling() -> None:
    indices = _gradient_indices()
    seven = analyze_temporal_zones(
        indices=indices,
        requested_zone_count=7,
        minimum_pixels_per_zone=6,
    )
    three = analyze_temporal_zones(
        indices=indices,
        requested_zone_count=3,
        minimum_pixels_per_zone=6,
    )

    assert seven.selected_zone_count == 7
    assert len(seven.zones) == 7
    assert three.selected_zone_count == 3
    assert len(three.zones) == 3
    assert seven.label_grid != three.label_grid
    with pytest.raises(ValueError, match="between 2 and 7"):
        analyze_temporal_zones(indices=indices, requested_zone_count=8)


def test_requested_count_is_capped_when_a_small_field_cannot_support_it() -> None:
    result = analyze_temporal_zones(
        indices=_gradient_indices(height=6, width=8),
        requested_zone_count=7,
        minimum_pixels_per_zone=12,
    )

    assert result.selected_zone_count <= 4
    assert result.selection.mode == "requested_capped"
    assert "suporte mínimo" in result.selection.reason_pt


def test_invalid_pixels_remain_unlabelled_and_insufficient_data_is_explicit() -> None:
    bands = {
        "B04": np.full((3, 8, 8), 0.2),
        "B05": np.full((3, 8, 8), 0.3),
        "B08": np.full((3, 8, 8), 0.7),
        "B11": np.full((3, 8, 8), 0.4),
    }
    # Add a field-scale gradient so two relative clusters are supported.
    bands["B08"] += np.linspace(0.0, 0.2, 8)[np.newaxis, np.newaxis, :]
    scl = np.full((3, 8, 8), 4)
    scl[:, :2, :2] = 9
    result = analyze_temporal_zones(
        bands=bands,
        scl=scl,
        requested_zone_count=2,
        minimum_pixels_per_zone=6,
    )

    assert all(result.label_grid[row][column] is None for row in range(2) for column in range(2))
    assert result.quality.usable_pixel_count == 60

    all_cloud = np.full((3, 8, 8), 9)
    with pytest.raises(InsufficientDataError, match="No pixels"):
        analyze_temporal_zones(bands=bands, scl=all_cloud)


def test_zone_content_contains_no_causal_diagnosis() -> None:
    result = analyze_temporal_zones(
        indices=_two_zone_indices(),
        requested_zone_count=2,
    )
    zone_text = json.dumps([as_zone for as_zone in result.to_dict()["zones"]]).lower()
    forbidden = ("praga", "doença", "solo", "água", "pest", "disease", "soil", "water")

    assert not any(term in zone_text for term in forbidden)
    assert result.scope["diagnostic"] is False
