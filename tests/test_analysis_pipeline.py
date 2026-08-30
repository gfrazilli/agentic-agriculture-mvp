import json
import logging
from datetime import UTC, date, datetime, timedelta

import numpy as np

from agriculture.adapters import (
    InMemoryAgricultureRepository,
    InMemoryArtifactStore,
    InMemoryTaskQueue,
)
from agriculture.api.models import AnalysisCreateInput, FieldCreateInput, FieldPatchInput
from agriculture.domain import AnalysisStatus
from agriculture.schemas import AnalysisProgress, AnalysisStage, GeoJSONPoint, GeoJSONPolygon
from agriculture.services.application import AgricultureService
from agriculture.services.idempotency import IdempotencyContext
from geospatial.cog import MultibandWindow, RasterWindow
from geospatial.earth_search import Sentinel2Scene
from geospatial.pipeline import AnalysisPipeline, _geometry_vertex_count, _zone_geometry

NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
TRANSFORM = (0.001, 0.0, -48.900, 0.0, -0.001, -23.970)


def _context(name: str) -> IdempotencyContext:
    return IdempotencyContext(scoped_key=f"pipeline-{name}", request_digest=f"digest-{name}")


def _scenes(count: int = 3) -> tuple[Sentinel2Scene, ...]:
    result = []
    for index in range(count):
        scene_id = f"S2A_PIPELINE_{index + 1}"
        result.append(
            Sentinel2Scene(
                id=scene_id,
                captured_at=NOW - timedelta(days=30 * (count - index)),
                cloud_cover=float(index + 1),
                assets={
                    "red": f"https://sentinel-cogs.s3.amazonaws.com/{scene_id}-red.tif",
                    "rededge1": f"https://sentinel-cogs.s3.amazonaws.com/{scene_id}-re.tif",
                    "nir": f"https://sentinel-cogs.s3.amazonaws.com/{scene_id}-nir.tif",
                    "swir16": f"https://sentinel-cogs.s3.amazonaws.com/{scene_id}-swir.tif",
                    "scl": f"https://sentinel-cogs.s3.amazonaws.com/{scene_id}-scl.tif",
                },
                geometry=None,
                bbox=(-48.91, -23.99, -48.88, -23.96),
                properties={},
            )
        )
    return tuple(result)


class FakeClient:
    def __init__(self, scenes):
        self.scenes = tuple(scenes)

    def search_scenes(self, **kwargs):  # noqa: ARG002
        return self.scenes


class FailingClient:
    def search_scenes(self, **kwargs):  # noqa: ARG002
        raise RuntimeError("Bearer private-worker-token geometry=-48.9,-23.9")


class FakeReader:
    def __init__(self, scenes):
        self.time_by_scene = {scene.id: index for index, scene in enumerate(scenes)}

    def read_required_bands(self, assets, **kwargs):  # noqa: ARG002
        scene_id = next(name for name in self.time_by_scene if name in assets["red"])
        time_index = self.time_by_scene[scene_id]
        shape = (12, 16)
        red = np.full(shape, 0.20, dtype=np.float32)
        red_edge = np.full(shape, 0.28, dtype=np.float32)
        swir = np.full(shape, 0.35, dtype=np.float32)
        nir = np.full(shape, 0.32 + 0.08 * time_index, dtype=np.float32)
        nir[:, 8:] = 0.48 + 0.12 * time_index
        scl = np.full(shape, 4.0, dtype=np.float32)
        arrays = {"B04": red, "B05": red_edge, "B08": nir, "B11": swir, "SCL": scl}
        return MultibandWindow(
            bands={
                name: RasterWindow(
                    data=data,
                    valid_mask=np.ones(shape, dtype=bool),
                    transform=TRANSFORM,
                    crs="EPSG:4326",
                    nodata=None,
                )
                for name, data in arrays.items()
            }
        )


class MaskedFakeReader(FakeReader):
    def __init__(self, scenes, masks_by_scene):
        super().__init__(scenes)
        self.masks_by_scene = dict(masks_by_scene)

    def read_required_bands(self, assets, **kwargs):
        scene_id = next(name for name in self.time_by_scene if name in assets["red"])
        window = super().read_required_bands(assets, **kwargs)
        supplied_mask = self.masks_by_scene.get(scene_id)
        if supplied_mask is None:
            return window
        mask = np.asarray(supplied_mask, dtype=bool)
        if mask.shape != window.shape:
            raise ValueError("The test validity mask must match the raster shape.")
        return MultibandWindow(
            bands={
                name: RasterWindow(
                    data=(np.where(mask, band.data, 9.0) if name == "SCL" else band.data),
                    valid_mask=band.valid_mask,
                    transform=band.transform,
                    crs=band.crs,
                    nodata=band.nodata,
                )
                for name, band in window.bands.items()
            }
        )


class ProjectedFakeReader(FakeReader):
    def read_required_bands(self, assets, **kwargs):
        window = super().read_required_bands(assets, **kwargs)
        rasterio_warp = __import__("rasterio.warp", fromlist=["transform_bounds"])
        west, south, east, north = rasterio_warp.transform_bounds(
            "EPSG:4326",
            "EPSG:3857",
            -48.900,
            -23.982,
            -48.884,
            -23.970,
        )
        transform = (
            (east - west) / window.shape[1],
            0.0,
            west,
            0.0,
            (south - north) / window.shape[0],
            north,
        )
        return MultibandWindow(
            bands={
                name: RasterWindow(
                    data=band.data,
                    valid_mask=band.valid_mask,
                    transform=transform,
                    crs="EPSG:3857",
                    nodata=band.nodata,
                )
                for name, band in window.bands.items()
            }
        )


class NonFiniteIndexFakeReader(FakeReader):
    def __init__(self, scenes, target_scene_id):
        super().__init__(scenes)
        self.target_scene_id = target_scene_id

    def read_required_bands(self, assets, **kwargs):
        scene_id = next(name for name in self.time_by_scene if name in assets["red"])
        window = super().read_required_bands(assets, **kwargs)
        if scene_id != self.target_scene_id:
            return window
        nir = window.bands["B08"].data
        return MultibandWindow(
            bands={
                name: RasterWindow(
                    data=(-nir if name in {"B04", "B05", "B11"} else band.data),
                    valid_mask=band.valid_mask,
                    transform=band.transform,
                    crs=band.crs,
                    nodata=band.nodata,
                )
                for name, band in window.bands.items()
            }
        )


def _queued_analysis(repository):
    service = AgricultureService(
        repository,
        InMemoryTaskQueue(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    created = service.create_field(
        FieldCreateInput(
            name="Talhão temporal",
            crop="soja",
            season_start=date(2026, 4, 1),
            season_end=date(2026, 8, 1),
            estimated_area_ha=2.0,
            reference_location=GeoJSONPoint(coordinates=(-48.892, -23.976)),
        ),
        _context("field"),
    )
    boundary = GeoJSONPolygon(
        coordinates=(
            (
                (-48.899, -23.971),
                (-48.885, -23.971),
                (-48.885, -23.981),
                (-48.899, -23.981),
                (-48.899, -23.971),
            ),
        )
    )
    field = service.patch_field(
        created.data["id"],
        FieldPatchInput(boundary=boundary, boundary_confirmed=True),
    )
    queued = service.create_analysis(
        AnalysisCreateInput(field_id=field.id, requested_zone_count=2),
        _context("analysis"),
        actor_id="pipeline-test",
    )
    return queued.data["id"]


def test_pipeline_completes_with_real_contract_and_auditable_artifacts(caplog, monkeypatch):
    caplog.set_level("INFO", logger="geospatial.pipeline")
    monkeypatch.setattr(logging.getLogger("geospatial.pipeline"), "propagate", True)
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    artifacts = InMemoryArtifactStore()
    analysis_id = _queued_analysis(repository)
    scenes = _scenes()
    pipeline = AnalysisPipeline(
        repository,
        artifacts,
        client=FakeClient(scenes),
        reader=FakeReader(scenes),
        clock=lambda: NOW,
        target_scene_count=3,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)

    assert outcome.status == "completed"
    assert outcome.scene_count == 3
    assert outcome.zone_count == 2
    assert stored is not None
    assert stored.status is AnalysisStatus.COMPLETED
    assert stored.result is not None
    assert stored.result.provenance.provider.startswith("EU/ESA/Copernicus")
    assert [zone.relative_label.value for zone in stored.result.zones] == [
        "lower_than_field",
        "higher_than_field",
    ]
    assert all(zone.boundary.type == "Polygon" for zone in stored.result.zones)
    assert artifacts.exists(f"analyses/{analysis_id}/zones.geojson")
    assert artifacts.exists(f"analyses/{analysis_id}/zone-map.svg")
    assert artifacts.exists(f"analyses/{analysis_id}/processing-report.json")
    feature_collection = json.loads(artifacts.get_bytes(f"analyses/{analysis_id}/zones.geojson"))
    assert feature_collection["type"] == "FeatureCollection"
    assert "crs" not in feature_collection["metadata"]
    assert [feature["geometry"] for feature in feature_collection["features"]] == [
        zone.boundary.model_dump(mode="json") for zone in stored.result.zones
    ]
    assert [feature["properties"]["area_ha"] for feature in feature_collection["features"]] == [
        zone.area_ha for zone in stored.result.zones
    ]
    assert len(stored.result.model_dump_json().encode()) < 500_000

    replay = pipeline.run(analysis_id)
    assert replay.status == "already_completed"

    messages = dict.fromkeys(
        record.getMessage() for record in caplog.records if record.name == "geospatial.pipeline"
    )
    events = [json.loads(message) for message in messages]
    assert {event["event"] for event in events} >= {
        "sentinel_pipeline.started",
        "sentinel_pipeline.stage",
        "sentinel_pipeline.scenes_selected",
        "sentinel_pipeline.completed",
        "sentinel_pipeline.skipped",
    }
    assert all(event["execution_id"] == analysis_id for event in events)
    assert [event["stage"] for event in events if event["event"] == "sentinel_pipeline.stage"] == [
        "acquiring_scenes",
        "computing_indices",
        "clustering_zones",
        "generating_explanation",
    ]
    selected = next(
        event for event in events if event["event"] == "sentinel_pipeline.scenes_selected"
    )
    assert selected["scene_ids"] == [scene.id for scene in scenes]
    assert "coordinates" not in caplog.text


def test_pipeline_discards_unusable_scenes_and_keeps_outputs_aligned(caplog, monkeypatch):
    caplog.set_level("INFO", logger="geospatial.pipeline")
    monkeypatch.setattr(logging.getLogger("geospatial.pipeline"), "propagate", True)
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    artifacts = InMemoryArtifactStore()
    analysis_id = _queued_analysis(repository)
    scenes = _scenes(5)
    empty_mask = np.zeros((12, 16), dtype=bool)
    isolated_mask = np.zeros((12, 16), dtype=bool)
    isolated_mask[:, :4] = True
    shared_mask = np.zeros((12, 16), dtype=bool)
    shared_mask[:, 4:] = True
    discarded = (scenes[1], scenes[3])
    retained = (scenes[0], scenes[2], scenes[4])
    pipeline = AnalysisPipeline(
        repository,
        artifacts,
        client=FakeClient(scenes),
        reader=MaskedFakeReader(
            scenes,
            {
                scenes[0].id: shared_mask,
                scenes[1].id: isolated_mask,
                scenes[2].id: shared_mask,
                scenes[3].id: empty_mask,
                scenes[4].id: shared_mask,
            },
        ),
        clock=lambda: NOW,
        target_scene_count=5,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)

    assert outcome.status == "completed"
    assert outcome.scene_count == len(retained)
    assert stored is not None
    assert stored.status is AnalysisStatus.COMPLETED
    assert stored.result is not None
    retained_ids = tuple(scene.id for scene in retained)
    assert tuple(scene.scene_id for scene in stored.result.scenes) == retained_ids
    assert stored.result.provenance.scene_ids == retained_ids
    assert all(
        tuple(point.scene_id for point in zone.trajectory) == retained_ids
        for zone in stored.result.zones
    )

    for scene in retained:
        assert artifacts.exists(f"analyses/{analysis_id}/scenes/{scene.id}-ndvi.svg")
    for scene in discarded:
        assert not artifacts.exists(f"analyses/{analysis_id}/scenes/{scene.id}-ndvi.svg")
    report = json.loads(artifacts.get_bytes(f"analyses/{analysis_id}/processing-report.json"))
    assert report["scene_ids"] == list(retained_ids)
    zones = json.loads(artifacts.get_bytes(f"analyses/{analysis_id}/zones.geojson"))
    assert len(zones["features"]) == stored.result.selected_zone_count

    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "geospatial.pipeline"
    ]
    discarded_event = next(
        event for event in events if event["event"] == "sentinel_pipeline.scenes_discarded"
    )
    assert discarded_event["status"] == "discarded"
    assert discarded_event["scene_count"] == len(discarded)
    assert discarded_event["scene_ids"] == [scene.id for scene in discarded]
    selected_event = next(
        event for event in events if event["event"] == "sentinel_pipeline.scenes_selected"
    )
    assert selected_event["scene_count"] == len(retained)
    assert selected_event["scene_ids"] == list(retained_ids)
    assert "coordinates" not in caplog.text


def test_pipeline_fails_when_finite_scenes_share_no_temporally_usable_pixels(
    caplog,
    monkeypatch,
):
    caplog.set_level("INFO", logger="geospatial.pipeline")
    monkeypatch.setattr(logging.getLogger("geospatial.pipeline"), "propagate", True)
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    artifacts = InMemoryArtifactStore()
    analysis_id = _queued_analysis(repository)
    scenes = _scenes(2)
    left_only = np.zeros((12, 16), dtype=bool)
    left_only[:, :8] = True
    right_only = np.zeros((12, 16), dtype=bool)
    right_only[:, 8:] = True
    pipeline = AnalysisPipeline(
        repository,
        artifacts,
        client=FakeClient(scenes),
        reader=MaskedFakeReader(
            scenes,
            {
                scenes[0].id: left_only,
                scenes[1].id: right_only,
            },
        ),
        clock=lambda: NOW,
        target_scene_count=2,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)

    assert outcome.status == "failed"
    assert outcome.error_code == "INSUFFICIENT_SATELLITE_DATA"
    assert stored is not None
    assert stored.status is AnalysisStatus.FAILED
    assert stored.result is None
    assert stored.error is not None
    assert stored.error.retryable is False
    assert "Fewer than two scenes" in stored.error.message
    assert not artifacts.exists(f"analyses/{analysis_id}/zones.geojson")
    assert not artifacts.exists(f"analyses/{analysis_id}/zone-map.svg")
    assert not artifacts.exists(f"analyses/{analysis_id}/processing-report.json")
    assert all(
        not artifacts.exists(f"analyses/{analysis_id}/scenes/{scene.id}-ndvi.svg")
        for scene in scenes
    )

    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "geospatial.pipeline"
    ]
    discarded_event = next(
        event for event in events if event["event"] == "sentinel_pipeline.scenes_discarded"
    )
    assert discarded_event["scene_count"] == 2
    assert discarded_event["scene_ids"] == [scene.id for scene in scenes]
    assert not any(event["event"] == "sentinel_pipeline.scenes_selected" for event in events)
    assert "coordinates" not in caplog.text


def test_pipeline_discards_nonfinite_index_scene_and_completes_with_exactly_two():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    artifacts = InMemoryArtifactStore()
    analysis_id = _queued_analysis(repository)
    scenes = _scenes(3)
    pipeline = AnalysisPipeline(
        repository,
        artifacts,
        client=FakeClient(scenes),
        reader=NonFiniteIndexFakeReader(scenes, scenes[1].id),
        clock=lambda: NOW,
        target_scene_count=3,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)

    assert outcome.status == "completed"
    assert outcome.scene_count == 2
    assert stored is not None and stored.result is not None
    retained_ids = (scenes[0].id, scenes[2].id)
    assert tuple(scene.scene_id for scene in stored.result.scenes) == retained_ids
    assert stored.result.provenance.scene_ids == retained_ids
    assert all(
        tuple(point.scene_id for point in zone.trajectory) == retained_ids
        for zone in stored.result.zones
    )
    assert not artifacts.exists(f"analyses/{analysis_id}/scenes/{scenes[1].id}-ndvi.svg")


def test_pipeline_rezones_after_scene_has_no_pixels_in_one_zone(caplog, monkeypatch):
    caplog.set_level("INFO", logger="geospatial.pipeline")
    monkeypatch.setattr(logging.getLogger("geospatial.pipeline"), "propagate", True)
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    analysis_id = _queued_analysis(repository)
    scenes = _scenes(3)
    left_only = np.zeros((12, 16), dtype=bool)
    left_only[:, :8] = True
    pipeline = AnalysisPipeline(
        repository,
        InMemoryArtifactStore(),
        client=FakeClient(scenes),
        reader=MaskedFakeReader(scenes, {scenes[0].id: left_only}),
        clock=lambda: NOW,
        target_scene_count=3,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)

    assert outcome.status == "completed"
    assert outcome.scene_count == 2
    assert stored is not None and stored.result is not None
    retained_ids = (scenes[1].id, scenes[2].id)
    assert tuple(scene.scene_id for scene in stored.result.scenes) == retained_ids
    assert stored.result.provenance.scene_ids == retained_ids
    assert all(
        tuple(point.scene_id for point in zone.trajectory) == retained_ids
        for zone in stored.result.zones
    )
    assert all(
        np.isfinite([point.indices.ndvi, point.indices.ndre, point.indices.ndmi]).all()
        for zone in stored.result.zones
        for point in zone.trajectory
    )
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "geospatial.pipeline"
    ]
    clustered_discard = next(
        event
        for event in events
        if event["event"] == "sentinel_pipeline.scenes_discarded"
        and event["stage"] == "clustering_zones"
    )
    assert clustered_discard["scene_ids"] == [scenes[0].id]


def test_pipeline_zone_gaps_leaving_one_scene_are_nonretryable():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    artifacts = InMemoryArtifactStore()
    analysis_id = _queued_analysis(repository)
    scenes = _scenes(3)
    left_only = np.zeros((12, 16), dtype=bool)
    left_only[:, :8] = True
    right_only = np.zeros((12, 16), dtype=bool)
    right_only[:, 8:] = True
    pipeline = AnalysisPipeline(
        repository,
        artifacts,
        client=FakeClient(scenes),
        reader=MaskedFakeReader(
            scenes,
            {
                scenes[0].id: left_only,
                scenes[1].id: right_only,
            },
        ),
        clock=lambda: NOW,
        target_scene_count=3,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)

    assert outcome.status == "failed"
    assert outcome.error_code == "INSUFFICIENT_SATELLITE_DATA"
    assert outcome.retryable is False
    assert stored is not None and stored.error is not None
    assert stored.error.retryable is False
    assert "complete trajectories" in stored.error.message
    assert not artifacts.exists(f"analyses/{analysis_id}/zones.geojson")


def test_zone_geometry_preserves_projected_multipolygon_hole_and_dissolves_runs():
    projected = {
        "type": "MultiPolygon",
        "coordinates": [
            [
                [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)],
                [(25, 25), (25, 75), (75, 75), (75, 25), (25, 25)],
            ],
            [[(200, 0), (300, 0), (300, 100), (200, 100), (200, 0)]],
        ],
    }

    boundary = _zone_geometry(projected, source_crs="EPSG:3857", max_vertices=100)

    assert boundary.type == "MultiPolygon"
    assert len(boundary.coordinates) == 2
    assert sorted(len(polygon) for polygon in boundary.coordinates) == [1, 2]
    assert (
        max(
            abs(coordinate)
            for polygon in boundary.coordinates
            for ring in polygon
            for position in ring
            for coordinate in position
        )
        < 1
    )

    adjacent_runs = {
        "type": "MultiPolygon",
        "coordinates": [
            [[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]],
            [[(10, 0), (20, 0), (20, 10), (10, 10), (10, 0)]],
        ],
    }
    dissolved = _zone_geometry(adjacent_runs, source_crs="EPSG:3857", max_vertices=20)
    assert dissolved.type == "Polygon"
    assert len(dissolved.coordinates) == 1


def test_zone_geometry_simplification_honors_explicit_vertex_limit():
    shapely_geometry = __import__("shapely.geometry", fromlist=["Point"])
    dense = shapely_geometry.Point(0, 0).buffer(1, quad_segs=128)

    boundary = _zone_geometry(
        shapely_geometry.mapping(dense),
        source_crs="EPSG:4326",
        max_vertices=40,
    )
    rebuilt = shapely_geometry.shape(boundary.model_dump(mode="json"))

    assert _geometry_vertex_count(rebuilt) <= 40
    assert rebuilt.is_valid


def test_pipeline_writes_wgs84_geojson_from_projected_raster():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    artifacts = InMemoryArtifactStore()
    analysis_id = _queued_analysis(repository)
    scenes = _scenes(3)
    pipeline = AnalysisPipeline(
        repository,
        artifacts,
        client=FakeClient(scenes),
        reader=ProjectedFakeReader(scenes),
        clock=lambda: NOW,
        target_scene_count=3,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)
    artifact = json.loads(artifacts.get_bytes(f"analyses/{analysis_id}/zones.geojson"))

    assert outcome.status == "completed"
    assert stored is not None and stored.result is not None
    assert "crs" not in artifact["metadata"]
    assert [feature["geometry"] for feature in artifact["features"]] == [
        zone.boundary.model_dump(mode="json") for zone in stored.result.zones
    ]
    positions = [
        position
        for feature in artifact["features"]
        for polygon in (
            [feature["geometry"]["coordinates"]]
            if feature["geometry"]["type"] == "Polygon"
            else feature["geometry"]["coordinates"]
        )
        for ring in polygon
        for position in ring
    ]
    assert all(-49.0 < longitude < -48.0 for longitude, _latitude in positions)
    assert all(-25.0 < latitude < -23.0 for _longitude, latitude in positions)


def test_pipeline_persists_explicit_insufficient_data_failure():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    analysis_id = _queued_analysis(repository)
    scenes = _scenes(1)
    pipeline = AnalysisPipeline(
        repository,
        InMemoryArtifactStore(),
        client=FakeClient(scenes),
        reader=FakeReader(scenes),
        clock=lambda: NOW,
        target_scene_count=2,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)

    assert outcome.status == "failed"
    assert outcome.error_code == "INSUFFICIENT_SATELLITE_DATA"
    assert stored is not None
    assert stored.status is AnalysisStatus.FAILED
    assert stored.error is not None
    assert stored.error.retryable is False


def test_pipeline_failure_log_excludes_exception_message_and_geometry(caplog, monkeypatch):
    caplog.set_level("INFO", logger="geospatial.pipeline")
    monkeypatch.setattr(logging.getLogger("geospatial.pipeline"), "propagate", True)
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    analysis_id = _queued_analysis(repository)
    pipeline = AnalysisPipeline(
        repository,
        InMemoryArtifactStore(),
        client=FailingClient(),
        reader=FakeReader(_scenes()),
        clock=lambda: NOW,
        target_scene_count=3,
        max_dimension=64,
    )

    outcome = pipeline.run(analysis_id)
    stored = repository.get_analysis(analysis_id)

    assert outcome.status == "failed"
    assert stored is not None
    assert stored.error is not None
    assert stored.error.message == (
        "The analysis could not be completed because of an unexpected processing error."
    )
    assert "private-worker-token" not in stored.error.message
    assert "-48.9" not in stored.error.message
    failure = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if "sentinel_pipeline.failed" in record.getMessage()
    )
    assert failure["execution_id"] == analysis_id
    assert failure["error_code"] == "ANALYSIS_PIPELINE_FAILED"
    assert failure["error_type"] == "RuntimeError"
    assert failure["retryable"] is True
    assert "private-worker-token" not in caplog.text
    assert "-48.9" not in caplog.text


def test_running_lease_retries_then_stale_work_is_recovered():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    analysis_id = _queued_analysis(repository)
    queued = repository.get_analysis(analysis_id)
    assert queued is not None
    running_progress = AnalysisProgress(
        percent=10,
        stage=AnalysisStage.ACQUIRING_SCENES,
        message_pt="Buscando observações Sentinel-2 da safra.",
        message_en="Finding Sentinel-2 observations for the season.",
        updated_at=NOW,
    )
    running = queued.model_copy(
        update={
            "status": AnalysisStatus.RUNNING,
            "progress": running_progress,
            "updated_at": NOW,
        }
    )
    repository.save_analysis(running)
    scenes = _scenes()
    clock = [NOW]
    pipeline = AnalysisPipeline(
        repository,
        InMemoryArtifactStore(),
        client=FakeClient(scenes),
        reader=FakeReader(scenes),
        clock=lambda: clock[0],
        target_scene_count=3,
        max_dimension=64,
    )

    active = pipeline.run(analysis_id)

    assert active.status == "already_running"
    assert active.retryable is True

    clock[0] = NOW + timedelta(minutes=21)

    recovered = pipeline.run(analysis_id)

    assert recovered.status == "completed"
    stored = repository.get_analysis(analysis_id)
    assert stored is not None
    assert stored.status is AnalysisStatus.COMPLETED
