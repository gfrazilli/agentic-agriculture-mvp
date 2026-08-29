from datetime import UTC, date, datetime, timedelta

import numpy as np

from agriculture.adapters import (
    InMemoryAgricultureRepository,
    InMemoryArtifactStore,
    InMemoryTaskQueue,
)
from agriculture.api.models import AnalysisCreateInput, FieldCreateInput, FieldPatchInput
from agriculture.domain import AnalysisStatus
from agriculture.schemas import GeoJSONPoint, GeoJSONPolygon
from agriculture.services.application import AgricultureService
from agriculture.services.idempotency import IdempotencyContext
from geospatial.cog import MultibandWindow, RasterWindow
from geospatial.earth_search import Sentinel2Scene
from geospatial.pipeline import AnalysisPipeline

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


def test_pipeline_completes_with_real_contract_and_auditable_artifacts():
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

    replay = pipeline.run(analysis_id)
    assert replay.status == "already_completed"


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
