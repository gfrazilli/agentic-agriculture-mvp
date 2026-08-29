import json
from dataclasses import replace
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
    suggestion_debug_payload,
)
from geospatial.cog import RasterWindow
from geospatial.earth_search import Sentinel2Scene

NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


def _scene() -> Sentinel2Scene:
    return Sentinel2Scene(
        id="S2A_REAL_SCENE",
        captured_at=datetime(2026, 8, 24, 13, tzinfo=UTC),
        cloud_cover=8.5,
        assets={
            "red": "https://bucket.amazonaws.com/red.tif",
            "nir": "https://bucket.amazonaws.com/nir.tif",
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
    def read(self, url, **kwargs):  # noqa: ARG002
        red = np.full((20, 20), 2_000.0, dtype=np.float32)
        nir = np.full((20, 20), 3_000.0, dtype=np.float32)
        red[7:13, 7:13] = 1_000.0
        nir[7:13, 7:13] = 5_000.0
        data = red if url.endswith("red.tif") else nir
        return RasterWindow(
            data=data,
            valid_mask=np.ones((20, 20), dtype=bool),
            transform=(10.0, 0.0, 0.0, 0.0, -10.0, 0.0),
            crs="EPSG:32722",
            nodata=None,
        )


def test_real_boundary_service_uses_scene_pixels_and_keeps_provenance():
    client = FakeClient()
    service = SentinelBoundaryService(client=client, reader=FakeReader(), clock=lambda: NOW)

    acquired = service.suggest(
        reference_location=(-48.88, -23.98),
        estimated_area_ha=0.36,
    )

    assert acquired.result.used_fallback is False
    assert len(acquired.result.candidates) == 3
    assert acquired.scene_id == "S2A_REAL_SCENE"
    assert acquired.provenance["catalog_provider"] == "Element 84 Earth Search"
    assert client.query["max_cloud_cover"] == 35.0
    payload = suggestion_debug_payload(acquired)
    assert payload["candidates"][0]["rank"] == 1


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
    assert acquired.scene_id is None


def test_tile_without_pixels_falls_through_to_next_intersecting_scene():
    first = replace(
        _scene(),
        id="S2A_EDGE_TILE",
        cloud_cover=1.0,
        assets={
            "red": "https://bucket.amazonaws.com/edge-red.tif",
            "nir": "https://bucket.amazonaws.com/edge-nir.tif",
        },
    )

    class TileAwareReader(FakeReader):
        def read(self, url, **kwargs):
            if "edge-" in url:
                raise ValueError("requested bounds outside this tile")
            return super().read(url, **kwargs)

    service = SentinelBoundaryService(
        client=FakeClient((first, _scene())),
        reader=TileAwareReader(),
        clock=lambda: NOW,
    )

    acquired = service.suggest(
        reference_location=(-48.88, -23.98),
        estimated_area_ha=0.36,
    )

    assert acquired.scene_id == "S2A_REAL_SCENE"
    assert acquired.result.used_fallback is False


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
