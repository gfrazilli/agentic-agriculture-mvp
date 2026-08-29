import json
from datetime import UTC, date, datetime

import numpy as np

from agriculture.adapters import InMemoryAgricultureRepository, InMemoryTaskQueue
from agriculture.api.models import FieldCreateInput
from agriculture.ports.boundaries import BoundaryProposal
from agriculture.schemas import BoundarySource, BoundarySuggestion, GeoJSONPoint, GeoJSONPolygon
from agriculture.services.application import AgricultureService
from agriculture.services.idempotency import IdempotencyContext
from geospatial.boundary_service import (
    EarthSearchBoundaryProvider,
    SentinelBoundaryService,
    _reference_pixel,
    suggestion_debug_payload,
)
from geospatial.cog import MultibandWindow, RasterWindow
from geospatial.earth_search import Sentinel2Scene

NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


def _scene(
    *,
    scene_id: str = "S2A_REAL_SCENE",
    cloud_cover: float = 8.5,
    asset_prefix: str = "clear",
) -> Sentinel2Scene:
    return Sentinel2Scene(
        id=scene_id,
        captured_at=datetime(2026, 8, 24, 13, tzinfo=UTC),
        cloud_cover=cloud_cover,
        assets={
            "red": f"https://bucket.amazonaws.com/{asset_prefix}-red.tif",
            "rededge1": f"https://bucket.amazonaws.com/{asset_prefix}-rededge1.tif",
            "nir": f"https://bucket.amazonaws.com/{asset_prefix}-nir.tif",
            "swir16": f"https://bucket.amazonaws.com/{asset_prefix}-swir16.tif",
            "scl": f"https://bucket.amazonaws.com/{asset_prefix}-scl.tif",
        },
        geometry=None,
        bbox=(-49.0, -24.1, -48.8, -23.9),
        properties={},
    )


class FakeClient:
    def __init__(self, scenes=None, *, fail: bool = False):
        self.scenes = tuple(scenes) if scenes is not None else (_scene(),)
        self.fail = fail
        self.query = None

    def search(self, **kwargs):
        self.query = kwargs
        if self.fail:
            raise RuntimeError("catalog unavailable")
        return self.scenes


class FakeReader:
    def __init__(self):
        self.calls = []

    def read_required_bands(self, assets, **kwargs):
        self.calls.append({"assets": assets, **kwargs})
        red_url = assets["red"]
        if "edge-" in red_url:
            raise ValueError("requested bounds outside this tile")

        red = np.full((20, 20), 2_000.0, dtype=np.float32)
        red_edge = np.full((20, 20), 2_200.0, dtype=np.float32)
        nir = np.full((20, 20), 3_000.0, dtype=np.float32)
        swir = np.full((20, 20), 2_500.0, dtype=np.float32)
        scl = np.full((20, 20), 4.0, dtype=np.float32)
        if "constant-" not in red_url:
            red[7:13, 7:13] = 1_000.0
            red_edge[7:13, 7:13] = 1_200.0
            nir[7:13, 7:13] = 5_000.0
            swir[7:13, 7:13] = 1_400.0
        if "cloudy-" in red_url:
            scl[5:16, 5:16] = 9.0

        transform = (0.0001, 0.0, -48.881, 0.0, -0.0001, -23.979)
        valid = np.ones((20, 20), dtype=bool)

        def window(data):
            return RasterWindow(
                data=data,
                valid_mask=valid,
                transform=transform,
                crs="EPSG:4326",
                nodata=None,
            )

        return MultibandWindow(
            bands={
                "B04": window(red),
                "B05": window(red_edge),
                "B08": window(nir),
                "B11": window(swir),
                "SCL": window(scl),
            }
        )


def test_real_boundary_service_uses_scene_pixels_and_keeps_provenance():
    client = FakeClient()
    reader = FakeReader()
    service = SentinelBoundaryService(client=client, reader=reader, clock=lambda: NOW)

    acquired = service.suggest(
        reference_location=(-48.88, -23.98),
        estimated_area_ha=0.36,
    )

    assert acquired.result.used_fallback is False
    assert len(acquired.result.candidates) == 3
    assert acquired.scene_id == "S2A_REAL_SCENE"
    assert acquired.provenance["catalog_provider"] == "Element 84 Earth Search"
    assert client.query["max_cloud_cover"] == 35.0
    assert client.query["limit"] == 12
    assert len(reader.calls) == 1
    payload = suggestion_debug_payload(acquired)
    assert payload["candidates"][0]["rank"] == 1
    assert payload["debug"]["attempts"][0]["status"] == "selected"


def test_catalog_failure_returns_editable_geometric_fallback():
    service = SentinelBoundaryService(
        client=FakeClient(fail=True),
        reader=FakeReader(),
        clock=lambda: NOW,
    )

    acquired = service.suggest(
        reference_location=(-48.88, -23.98),
        estimated_area_ha=1.2,
    )

    assert acquired.result.used_fallback is True
    assert acquired.result.reason == "sentinel_imagery_temporarily_unavailable"
    assert acquired.result.candidates[0].requires_confirmation is True
    assert acquired.result.candidates[0].scores.total <= 0.20
    assert acquired.scene_id is None


def test_tile_without_pixels_falls_through_to_next_intersecting_scene():
    first = _scene(
        scene_id="S2A_EDGE_TILE",
        cloud_cover=1.0,
        asset_prefix="edge",
    )

    service = SentinelBoundaryService(
        client=FakeClient((first, _scene())),
        reader=FakeReader(),
        clock=lambda: NOW,
    )

    acquired = service.suggest(
        reference_location=(-48.88, -23.98),
        estimated_area_ha=0.36,
    )

    assert acquired.scene_id == "S2A_REAL_SCENE"
    assert acquired.result.used_fallback is False
    assert acquired.debug["attempts"][0]["status"] == "error"


def test_local_scl_cloud_mask_rejects_scene_and_uses_next_clear_scene():
    cloudy = _scene(
        scene_id="S2A_LOCAL_CLOUD",
        cloud_cover=1.0,
        asset_prefix="cloudy",
    )
    clear = _scene(scene_id="S2B_CLEAR", cloud_cover=5.0)
    service = SentinelBoundaryService(
        client=FakeClient((clear, cloudy)),
        reader=FakeReader(),
        clock=lambda: NOW,
    )

    acquired = service.suggest(
        reference_location=(-48.88, -23.98),
        estimated_area_ha=0.36,
    )

    assert acquired.scene_id == "S2B_CLEAR"
    first_attempt = acquired.debug["attempts"][0]
    assert first_attempt["scene_id"] == "S2A_LOCAL_CLOUD"
    assert first_attempt["reason"] == "insufficient_local_clear_coverage"
    assert first_attempt["local_clear_coverage"] < 0.60
    assert acquired.debug["masked_scl_classes"] == [0, 1, 3, 8, 9, 10, 11]


def test_scene_that_generates_engine_fallback_does_not_block_next_scene():
    spectrally_constant = _scene(
        scene_id="S2A_CONSTANT",
        cloud_cover=1.0,
        asset_prefix="constant",
    )
    clear = _scene(scene_id="S2B_USEFUL", cloud_cover=7.0)
    service = SentinelBoundaryService(
        client=FakeClient((clear, spectrally_constant)),
        reader=FakeReader(),
        clock=lambda: NOW,
    )

    acquired = service.suggest(
        reference_location=(-48.88, -23.98),
        estimated_area_ha=0.36,
    )

    assert acquired.scene_id == "S2B_USEFUL"
    assert acquired.result.used_fallback is False
    assert acquired.debug["attempts"][0] == {
        "scene_id": "S2A_CONSTANT",
        "cloud_cover": 1.0,
        "local_clear_coverage": 1.0,
        "status": "fallback",
        "reason": "insufficient_valid_or_variable_spectral_data",
    }


def test_scene_attempts_are_bounded_and_final_fallback_keeps_debug_reason():
    scenes = tuple(
        _scene(
            scene_id=f"S2A_CONSTANT_{index}",
            cloud_cover=float(index),
            asset_prefix=f"constant-{index}",
        )
        for index in range(6)
    )
    service = SentinelBoundaryService(
        client=FakeClient(scenes),
        reader=FakeReader(),
        clock=lambda: NOW,
        max_scenes_to_try=2,
    )

    acquired = service.suggest(
        reference_location=(-48.88, -23.98),
        estimated_area_ha=0.36,
    )

    assert acquired.result.used_fallback is True
    assert acquired.result.reason == "no_sentinel_scene_produced_a_boundary"
    assert acquired.result.candidates[0].scores.total <= 0.20
    assert len(acquired.debug["attempts"]) == 2
    assert acquired.debug["scene_attempt_limit"] == 2


def test_reference_pixel_is_derived_from_location_and_real_affine_transform():
    raster = RasterWindow(
        data=np.ones((20, 20), dtype=np.float32),
        valid_mask=np.ones((20, 20), dtype=bool),
        transform=(0.0001, 0.0, -48.881, 0.0, -0.0001, -23.979),
        crs="EPSG:4326",
        nodata=None,
    )

    assert _reference_pixel((-48.8806, -23.9796), raster) == (6, 4)


def test_stable_boundary_endpoint_can_use_injected_real_provider():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    queue = InMemoryTaskQueue(clock=lambda: NOW)
    boundary = GeoJSONPolygon(
        coordinates=(((-48.89, -23.99), (-48.87, -23.99), (-48.87, -23.97), (-48.89, -23.99)),)
    )

    class Provider:
        def suggest(self, field):  # noqa: ARG002
            return BoundaryProposal(
                boundary=boundary,
                estimated_area_ha=1.0,
                confidence=0.91,
                source=BoundarySource.SENTINEL_2,
            )

    service = AgricultureService(
        repository,
        queue,
        boundary_provider=Provider(),
        clock=lambda: NOW,
    )
    created = service.create_field(
        FieldCreateInput(
            name="Talhão real",
            crop="soja",
            season_start=date(2026, 1, 1),
            season_end=date(2026, 5, 1),
            estimated_area_ha=1.0,
            reference_location=GeoJSONPoint(coordinates=(-48.88, -23.98)),
        ),
        IdempotencyContext(scoped_key="field-real", request_digest="field-digest"),
    )

    result = service.suggest_boundary(
        created.data["id"],
        IdempotencyContext(scoped_key="boundary-real", request_digest="boundary-digest"),
    )
    suggestion = BoundarySuggestion.model_validate_json(json.dumps(result.data))

    assert suggestion.boundary == boundary
    assert suggestion.confidence == 0.91
    assert suggestion.source is BoundarySource.SENTINEL_2


def test_earth_search_provider_maps_top_candidate_to_public_contract():
    service = SentinelBoundaryService(client=FakeClient(), reader=FakeReader(), clock=lambda: NOW)
    provider = EarthSearchBoundaryProvider(service)
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    application = AgricultureService(repository, InMemoryTaskQueue(), clock=lambda: NOW)
    created = application.create_field(
        FieldCreateInput(
            name="Talhão",
            crop="milho",
            season_start=date(2026, 1, 1),
            season_end=date(2026, 4, 1),
            estimated_area_ha=0.36,
            reference_location=GeoJSONPoint(coordinates=(-48.88, -23.98)),
        ),
        IdempotencyContext(scoped_key="provider-field", request_digest="provider-digest"),
    )
    field = repository.get_field(created.data["id"])
    assert field is not None

    proposal = provider.suggest(field)

    assert proposal.source is BoundarySource.SENTINEL_2
    assert proposal.estimated_area_ha > 0
    assert 0 <= proposal.confidence <= 1
