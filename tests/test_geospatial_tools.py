import asyncio
from datetime import UTC, datetime

import pytest

from geospatial.cog import UnsafeAssetURLError, validate_asset_url
from geospatial.earth_search import Sentinel2Scene
from geospatial.mcp_server import mcp
from geospatial.tools import GeospatialTools, InsufficientScenesError, resolve_required_band_assets


def _scene(day: int, *, complete: bool = True) -> Sentinel2Scene:
    assets = {
        "red": f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/red-{day}.tif",
        "rededge1": f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/re-{day}.tif",
        "nir": f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/nir-{day}.tif",
        "swir16": f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/swir-{day}.tif",
        "scl": f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/scl-{day}.tif",
    }
    if not complete:
        assets.pop("swir16")
    return Sentinel2Scene(
        id=f"scene-{day}",
        captured_at=datetime(2026, 1, day, 12, tzinfo=UTC),
        cloud_cover=float(day),
        assets=assets,
        geometry={"type": "Polygon", "coordinates": []},
        bbox=(-49.0, -24.0, -48.0, -23.0),
        properties={},
    )


class FakeClient:
    def __init__(self, scenes):
        self.scenes = tuple(scenes)

    def search_scenes(self, **kwargs):  # noqa: ARG002
        return self.scenes

    def search(self, **kwargs):  # noqa: ARG002
        return self.scenes

    def get_item(self, scene_id):
        return next(scene for scene in self.scenes if scene.id == scene_id)


def test_resolves_required_earth_search_asset_names():
    resolved = resolve_required_band_assets(_scene(1).assets)

    assert set(resolved) == {"B04", "B05", "B08", "B11", "SCL"}


def test_plan_filters_incomplete_scenes_and_spreads_dates():
    scenes = [_scene(day) for day in range(1, 10)] + [_scene(10, complete=False)]
    service = GeospatialTools(FakeClient(scenes))

    result = service.plan_observations(
        polygon={"type": "Polygon", "coordinates": []},
        start="2026-01-01",
        end="2026-01-31",
        scene_count=3,
    )

    assert [scene["id"] for scene in result["scenes"]] == ["scene-1", "scene-5", "scene-9"]
    assert result["provenance"]["data_producer"] == "EU/ESA/Copernicus"
    assert result["provenance"]["catalog_provider"] == "Element 84 Earth Search"


def test_plan_never_fabricates_missing_temporal_evidence():
    service = GeospatialTools(FakeClient([_scene(1), _scene(2, complete=False)]))

    with pytest.raises(InsufficientScenesError):
        service.plan_observations(
            polygon={"type": "Polygon", "coordinates": []},
            start="2026-01-01",
            end="2026-01-31",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://sentinel-cogs.s3.amazonaws.com/a.tif",
        "https://example.com/a.tif",
        "https://user:secret@sentinel-cogs.s3.amazonaws.com/a.tif",
        "file:///tmp/a.tif",
    ],
)
def test_cog_asset_url_allowlist_rejects_unsafe_targets(url):
    with pytest.raises(UnsafeAssetURLError):
        validate_asset_url(url)


def test_cog_asset_url_allowlist_accepts_public_sentinel_bucket():
    url = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/a.tif"

    assert validate_asset_url(url) == url


def test_private_mcp_registers_only_compact_geospatial_tools():
    tool_names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert tool_names == {
        "get_sentinel_scene",
        "plan_field_observations",
        "search_sentinel_scenes",
    }
