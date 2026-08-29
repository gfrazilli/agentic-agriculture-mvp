from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from geospatial.earth_search import (
    EarthSearchClient,
    EarthSearchProtocolError,
    EarthSearchRemoteError,
    EarthSearchTransportError,
    EarthSearchValidationError,
)

FIELD_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [-49.10, -24.10],
            [-49.00, -24.10],
            [-49.00, -24.00],
            [-49.10, -24.00],
            [-49.10, -24.10],
        ]
    ],
}

SCENE_FEATURE = {
    "type": "Feature",
    "id": "S2A_22JGN_20260828_0_L2A",
    "bbox": [-49.2, -24.2, -48.7, -23.7],
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[-49.2, -24.2], [-48.7, -24.2], [-48.7, -23.7], [-49.2, -24.2]]],
    },
    "properties": {
        "datetime": "2026-08-28T13:22:41.123Z",
        "eo:cloud_cover": 19.8,
    },
    "assets": {
        "blue": {"href": "https://public.example/scene/B02.tif"},
        "green": {"href": "https://public.example/scene/B03.tif"},
        "red": {
            "href": "https://public.example/scene/B04.tif",
            "raster:bands": [{"scale": 0.0001, "offset": -0.1}],
        },
        "nir": {"href": "https://public.example/scene/B08.tif"},
        "rededge1": {"href": "https://public.example/scene/B05.tif"},
        "swir16": {"href": "https://public.example/scene/B11.tif"},
        "swir22": {"href": "https://public.example/scene/B12.tif"},
        "scl": {"href": "https://public.example/scene/SCL.tif"},
        "visual": {"href": "https://public.example/scene/visual.tif"},
        "thumbnail": {"href": "https://public.example/scene/thumb.jpg"},
    },
}


def feature_collection(*features: dict[str, object]) -> dict[str, object]:
    return {"type": "FeatureCollection", "features": list(features), "links": []}


def test_search_builds_stac_payload_and_parses_public_cog_assets() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=feature_collection(SCENE_FEATURE))

    with EarthSearchClient(
        base_url="https://earth-search.test/v1/",
        transport=httpx.MockTransport(handler),
    ) as client:
        scenes = client.search_scenes(
            polygon=FIELD_POLYGON,
            start="2026-08-01",
            end="2026-08-29",
            limit=8,
            max_cloud_cover=30,
        )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url == httpx.URL("https://earth-search.test/v1/search")
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {
        "collections": ["sentinel-2-l2a"],
        "intersects": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-49.1, -24.1],
                    [-49.0, -24.1],
                    [-49.0, -24.0],
                    [-49.1, -24.0],
                    [-49.1, -24.1],
                ]
            ],
        },
        "datetime": "2026-08-01T00:00:00.000000Z/2026-08-29T23:59:59.999999Z",
        "limit": 8,
        "query": {"eo:cloud_cover": {"lte": 30.0}},
    }

    scene = scenes[0]
    assert scene.id == "S2A_22JGN_20260828_0_L2A"
    assert scene.scene_id == "S2A_22JGN_20260828_0_L2A"
    assert scene.captured_at == datetime(2026, 8, 28, 13, 22, 41, 123000, tzinfo=UTC)
    assert scene.acquired_at == datetime(2026, 8, 28, 13, 22, 41, 123000, tzinfo=UTC)
    assert scene.cloud_cover == 19.8
    assert set(scene.assets) == {
        "blue",
        "green",
        "red",
        "nir",
        "rededge1",
        "swir16",
        "swir22",
        "scl",
        "visual",
    }
    assert scene.asset_url("nir") == "https://public.example/scene/B08.tif"
    assert scene.calibration("red") == (0.0001, -0.1)
    assert scene.calibration("nir") is None
    assert scene.asset_url("thumbnail") is None
    assert scene.bbox == (-49.2, -24.2, -48.7, -23.7)
    assert scene.geometry == SCENE_FEATURE["geometry"]
    assert scene.properties["eo:cloud_cover"] == 19.8
    assert json.loads(json.dumps(scene.to_dict()))["captured_at"] == ("2026-08-28T13:22:41.123000Z")


def test_search_accepts_bbox_and_get_item_uses_stac_item_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=feature_collection(SCENE_FEATURE))
        return httpx.Response(200, json=SCENE_FEATURE)

    with EarthSearchClient(
        base_url="https://earth-search.test/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        scenes = client.search(
            bbox=(-49.2, -24.2, -48.7, -23.7),
            start="2026-08-01T00:00:00-03:00",
            end="2026-08-29T23:59:59-03:00",
            max_cloud_cover=25,
            limit=3,
        )
        item = client.get_item("S2A_22JGN_20260828_0_L2A")

    search_payload = json.loads(requests[0].content)
    assert search_payload["bbox"] == [-49.2, -24.2, -48.7, -23.7]
    assert "intersects" not in search_payload
    assert search_payload["datetime"] == ("2026-08-01T03:00:00.000000Z/2026-08-30T02:59:59.000000Z")
    assert scenes[0].id == item.id
    assert requests[1].method == "GET"
    assert requests[1].url == httpx.URL(
        "https://earth-search.test/v1/collections/sentinel-2-l2a/items/S2A_22JGN_20260828_0_L2A"
    )
    assert "authorization" not in requests[1].headers


def test_search_honors_result_limit_even_if_remote_returns_extra_features() -> None:
    second_feature = {
        **SCENE_FEATURE,
        "id": "S2B_22JGN_20260823_0_L2A",
        "properties": {**SCENE_FEATURE["properties"], "datetime": "2026-08-23T13:22:41Z"},
    }

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json=feature_collection(SCENE_FEATURE, second_feature),
        )
    )
    with EarthSearchClient(transport=transport) as client:
        scenes = client.search_scenes(
            polygon=FIELD_POLYGON,
            start="2026-08-01",
            end="2026-08-29",
            limit=1,
        )

    assert [scene.scene_id for scene in scenes] == ["S2A_22JGN_20260828_0_L2A"]


def test_429_is_retried_and_retry_after_is_capped() -> None:
    call_count = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(200, json=feature_collection(SCENE_FEATURE))

    with EarthSearchClient(
        transport=httpx.MockTransport(handler),
        max_retries=1,
        max_retry_after=1.5,
        sleep=sleeps.append,
    ) as client:
        scenes = client.search_scenes(
            polygon=FIELD_POLYGON,
            start="2026-08-01",
            end="2026-08-29",
        )

    assert len(scenes) == 1
    assert call_count == 2
    assert sleeps == [1.5]


def test_non_retryable_remote_error_is_raised_immediately() -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"detail": "bad request"})

    with EarthSearchClient(
        transport=httpx.MockTransport(handler),
        max_retries=3,
        sleep=lambda _seconds: pytest.fail("400 must not be retried"),
    ) as client:
        with pytest.raises(EarthSearchRemoteError, match="HTTP 400") as exc_info:
            client.search_scenes(
                polygon=FIELD_POLYGON,
                start="2026-08-01",
                end="2026-08-29",
            )

    assert exc_info.value.status_code == 400
    assert call_count == 1


def test_transport_errors_are_not_retried() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("offline", request=request)

    with EarthSearchClient(
        transport=httpx.MockTransport(handler),
        max_retries=3,
        sleep=lambda _seconds: pytest.fail("transport errors must not be retried"),
    ) as client:
        with pytest.raises(EarthSearchTransportError, match="ConnectError"):
            client.search_scenes(
                polygon=FIELD_POLYGON,
                start="2026-08-01",
                end="2026-08-29",
            )

    assert call_count == 1


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"polygon": {"type": "Point", "coordinates": [0, 0]}}, "type must be 'Polygon'"),
        (
            {
                "polygon": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
                }
            },
            "must be closed",
        ),
        ({"start": datetime(2026, 8, 1)}, "must include a timezone"),
        ({"start": "2026-08-30", "end": "2026-08-29"}, "must not be after"),
        ({"start": "2025-01-01", "end": "2026-08-29"}, "cannot exceed 366 days"),
        ({"limit": 0}, "limit must be between"),
        ({"limit": 101}, "limit must be between"),
        ({"max_cloud_cover": 101}, "must be between 0 and 100"),
    ],
)
def test_search_validates_inputs_before_network(
    overrides: dict[str, object],
    expected_message: str,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid searches must not reach the network")

    arguments: dict[str, object] = {
        "polygon": FIELD_POLYGON,
        "start": "2026-08-01",
        "end": "2026-08-29",
        "limit": 20,
        "max_cloud_cover": None,
    }
    arguments.update(overrides)

    with EarthSearchClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EarthSearchValidationError, match=expected_message):
            client.search_scenes(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"type": "FeatureCollection"}),
        httpx.Response(
            200,
            json=feature_collection({**SCENE_FEATURE, "properties": {"datetime": "nope"}}),
        ),
    ],
)
def test_invalid_stac_responses_raise_protocol_error(response: httpx.Response) -> None:
    with EarthSearchClient(transport=httpx.MockTransport(lambda _request: response)) as client:
        with pytest.raises(EarthSearchProtocolError):
            client.search_scenes(
                polygon=FIELD_POLYGON,
                start="2026-08-01",
                end="2026-08-29",
            )
