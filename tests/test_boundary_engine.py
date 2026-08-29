from __future__ import annotations

import numpy as np
import pytest

from geospatial.boundary import suggest_boundaries


def _synthetic_field() -> np.ndarray:
    grid = np.zeros((20, 20, 3), dtype=np.float64)
    grid[:, :, :] = (0.2, 0.25, 0.3)
    grid[7:13, 7:13, :] = (0.8, 0.65, 0.4)
    return grid


def _ring_area(ring: list[list[float]]) -> float:
    return abs(
        sum(
            first[0] * second[1] - second[0] * first[1]
            for first, second in zip(ring, ring[1:], strict=False)
        )
        / 2.0
    )


def test_returns_three_ranked_explainable_candidates() -> None:
    result = suggest_boundaries(
        _synthetic_field(),
        reference_location=(-48.88, -23.98),
        reference_pixel=(10, 10),
        resolution_m=10.0,
        estimated_area_ha=0.36,
    )

    assert result.used_fallback is False
    assert len(result.candidates) == 3
    assert [candidate.rank for candidate in result.candidates] == [1, 2, 3]
    totals = [candidate.scores.total for candidate in result.candidates]
    assert totals == sorted(totals, reverse=True)
    assert result.candidates[0].estimated_area_ha == pytest.approx(0.36)

    for candidate in result.candidates:
        assert candidate.editable is True
        assert candidate.requires_confirmation is True
        assert candidate.source == "spectral-region-growth"
        assert len(candidate.rationale) >= 4
        for score in candidate.scores.as_dict().values():
            assert 0.0 <= score <= 1.0


def test_candidate_polygons_are_closed_non_degenerate_and_deterministic() -> None:
    arguments = {
        "reference_location": (-48.88, -23.98),
        "reference_pixel": (10, 10),
        "resolution_m": 10.0,
        "estimated_area_ha": 0.36,
    }
    first = suggest_boundaries(_synthetic_field(), **arguments)
    second = suggest_boundaries(_synthetic_field(), **arguments)

    assert first == second
    for candidate in first.candidates:
        assert candidate.boundary["type"] == "Polygon"
        ring = candidate.boundary["coordinates"][0]
        assert len(ring) >= 4
        assert ring[0] == ring[-1]
        assert _ring_area(ring) > 0.0
        assert all(-180.0 <= point[0] <= 180.0 for point in ring)
        assert all(-90.0 <= point[1] <= 90.0 for point in ring)


def test_constant_data_uses_one_editable_geometric_fallback() -> None:
    result = suggest_boundaries(
        np.ones((8, 8), dtype=np.float64),
        reference_location=(-48.88, -23.98),
        resolution_m=10.0,
        estimated_area_ha=1.0,
    )

    assert result.used_fallback is True
    assert result.reason == "insufficient_valid_or_variable_spectral_data"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source == "geometric-fallback"
    assert candidate.estimated_area_ha == 1.0
    assert candidate.editable is True
    assert candidate.requires_confirmation is True
    assert candidate.boundary["coordinates"][0][0] == candidate.boundary["coordinates"][0][-1]


def test_empty_valid_mask_uses_fallback_without_numeric_warnings() -> None:
    result = suggest_boundaries(
        _synthetic_field(),
        reference_location=(-48.88, -23.98),
        resolution_m=10.0,
        estimated_area_ha=0.36,
        valid_mask=np.zeros((20, 20), dtype=bool),
    )

    assert result.used_fallback is True
    assert result.reason == "insufficient_valid_or_variable_spectral_data"


def test_validation_rejects_mismatched_mask_and_candidate_count() -> None:
    with pytest.raises(ValueError, match="valid_mask"):
        suggest_boundaries(
            _synthetic_field(),
            reference_location=(-48.88, -23.98),
            resolution_m=10.0,
            valid_mask=np.ones((3, 3), dtype=bool),
        )

    with pytest.raises(ValueError, match="max_candidates"):
        suggest_boundaries(
            _synthetic_field(),
            reference_location=(-48.88, -23.98),
            resolution_m=10.0,
            max_candidates=4,
        )
