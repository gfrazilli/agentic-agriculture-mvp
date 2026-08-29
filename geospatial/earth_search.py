"""Small, authentication-free client for Sentinel-2 scenes in Earth Search.

Earth Search exposes public STAC metadata whose assets point at Cloud Optimized
GeoTIFFs (COGs) in AWS Open Data.  This module deliberately handles discovery
only: raster window reads and vegetation-index calculations belong downstream.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from email.utils import parsedate_to_datetime
from typing import Any, Self
from urllib.parse import quote, urlparse

import httpx

EARTH_SEARCH_BASE_URL = "https://earth-search.aws.element84.com/v1"
SENTINEL_2_L2A_COLLECTION = "sentinel-2-l2a"
MAX_RESULTS = 100
MAX_SEARCH_INTERVAL = timedelta(days=366)
MAX_POLYGON_VERTICES = 5_000

_ASSET_ALIASES: Mapping[str, tuple[str, ...]] = {
    "blue": ("blue", "B02", "b02"),
    "green": ("green", "B03", "b03"),
    "red": ("red", "B04", "b04"),
    "nir": ("nir", "nir08", "B08", "b08"),
    "rededge1": ("rededge1", "B05", "b05"),
    "swir16": ("swir16", "B11", "b11"),
    "swir22": ("swir22", "B12", "b12"),
    "scl": ("scl", "SCL"),
    "visual": ("visual",),
}
_SCENE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}")


class EarthSearchError(RuntimeError):
    """Base error for Earth Search communication and response failures."""


class EarthSearchValidationError(ValueError):
    """Raised before a request when search parameters are unsafe or invalid."""


class EarthSearchTransportError(EarthSearchError):
    """Raised for a network failure; transport failures are not retried."""


class EarthSearchRemoteError(EarthSearchError):
    """Raised when Earth Search returns an unsuccessful HTTP status."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Earth Search returned HTTP {status_code}.")


class EarthSearchProtocolError(EarthSearchError):
    """Raised when a successful response does not follow the STAC contract."""


@dataclass(frozen=True, slots=True)
class Sentinel2Scene:
    """JSON-friendly metadata and public COG URLs for one Sentinel-2 scene."""

    id: str
    captured_at: datetime
    cloud_cover: float | None
    assets: dict[str, str]
    geometry: dict[str, Any] | None
    bbox: tuple[float, ...] | None
    properties: dict[str, Any]

    @property
    def scene_id(self) -> str:
        """Backward-compatible, explicit alias for the STAC item id."""

        return self.id

    @property
    def acquired_at(self) -> datetime:
        """Alias used by raster-processing code."""

        return self.captured_at

    def asset_url(self, name: str) -> str | None:
        """Return a normalized asset URL, if that band is available."""

        return self.assets.get(name)

    def to_dict(self) -> dict[str, Any]:
        """Return a structure accepted directly by JSON encoders."""

        return {
            "id": self.id,
            "captured_at": _format_datetime(self.captured_at),
            "cloud_cover": self.cloud_cover,
            "assets": dict(self.assets),
            "geometry": self.geometry,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "properties": dict(self.properties),
        }


class EarthSearchClient:
    """Synchronous client for bounded Sentinel-2 L2A STAC searches.

    No credentials or authentication headers are used.  ``max_retries`` counts
    additional attempts and applies only to HTTP 429 and 5xx responses.
    """

    def __init__(
        self,
        *,
        base_url: str = EARTH_SEARCH_BASE_URL,
        timeout: float | httpx.Timeout = 10.0,
        max_retries: int = 2,
        max_retry_after: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        parsed_base_url = urlparse(base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            raise EarthSearchValidationError("base_url must be an absolute HTTP(S) URL.")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int):
            raise EarthSearchValidationError("max_retries must be an integer.")
        if not 0 <= max_retries <= 5:
            raise EarthSearchValidationError("max_retries must be between 0 and 5.")
        if isinstance(max_retry_after, bool) or not isinstance(max_retry_after, (int, float)):
            raise EarthSearchValidationError("max_retry_after must be a number.")
        if not math.isfinite(max_retry_after) or not 0 <= max_retry_after <= 60:
            raise EarthSearchValidationError("max_retry_after must be between 0 and 60 seconds.")
        if client is not None and transport is not None:
            raise EarthSearchValidationError("Pass either client or transport, not both.")

        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._max_retry_after = float(max_retry_after)
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": "agentic-agriculture-mvp/0.1"},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the internally-created HTTP client."""

        if self._owns_client:
            self._client.close()

    def search_scenes(
        self,
        *,
        polygon: Mapping[str, Any],
        start: date | datetime | str,
        end: date | datetime | str,
        limit: int = 20,
        max_cloud_cover: float | None = None,
    ) -> tuple[Sentinel2Scene, ...]:
        """Find Sentinel-2 L2A scenes intersecting a field polygon.

        Date-only values cover their complete UTC day. Datetime values, including
        ISO datetime strings, must contain a timezone. Results are bounded to
        ``MAX_RESULTS`` and the interval to ``MAX_SEARCH_INTERVAL``.
        """

        return self._search(
            spatial_filter={"intersects": _validate_polygon(polygon)},
            start=start,
            end=end,
            limit=limit,
            max_cloud_cover=max_cloud_cover,
        )

    def search(
        self,
        *,
        bbox: tuple[float, float, float, float],
        start: date | datetime | str,
        end: date | datetime | str,
        limit: int = 20,
        max_cloud_cover: float | None = None,
    ) -> tuple[Sentinel2Scene, ...]:
        """Find scenes inside ``(west, south, east, north)`` bounds."""

        return self._search(
            spatial_filter={"bbox": _validate_bbox(bbox)},
            start=start,
            end=end,
            limit=limit,
            max_cloud_cover=max_cloud_cover,
        )

    def get_item(self, scene_id: str) -> Sentinel2Scene:
        """Fetch one Sentinel-2 L2A STAC item by its exact id."""

        if not isinstance(scene_id, str) or _SCENE_ID_PATTERN.fullmatch(scene_id) is None:
            raise EarthSearchValidationError("scene_id contains unsupported characters.")
        encoded_id = quote(scene_id, safe="")
        payload = self._request_json(
            "GET",
            f"{self._base_url}/collections/{SENTINEL_2_L2A_COLLECTION}/items/{encoded_id}",
        )
        return _parse_scene(payload)

    def _search(
        self,
        *,
        spatial_filter: Mapping[str, Any],
        start: date | datetime | str,
        end: date | datetime | str,
        limit: int,
        max_cloud_cover: float | None,
    ) -> tuple[Sentinel2Scene, ...]:
        start_at = _normalize_boundary(start, is_end=False)
        end_at = _normalize_boundary(end, is_end=True)
        _validate_interval(start_at, end_at)
        validated_limit = _validate_limit(limit)
        validated_cloud_cover = _validate_cloud_cover(max_cloud_cover)

        payload: dict[str, Any] = {
            "collections": [SENTINEL_2_L2A_COLLECTION],
            "datetime": f"{_format_datetime(start_at)}/{_format_datetime(end_at)}",
            "limit": validated_limit,
            **spatial_filter,
        }
        if validated_cloud_cover is not None:
            payload["query"] = {"eo:cloud_cover": {"lte": validated_cloud_cover}}

        response_payload = self._request_json(
            "POST",
            f"{self._base_url}/search",
            json_payload=payload,
        )
        scenes = _parse_feature_collection(response_payload)
        return scenes[:validated_limit]

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        attempt = 0
        while True:
            try:
                response = self._client.request(method, url, json=json_payload)
            except httpx.HTTPError as exc:
                raise EarthSearchTransportError(
                    f"Earth Search transport failed ({type(exc).__name__})."
                ) from exc

            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt < self._max_retries:
                    delay = _retry_delay(
                        response.headers.get("Retry-After"),
                        attempt=attempt,
                        maximum=self._max_retry_after,
                    )
                    self._sleep(delay)
                    attempt += 1
                    continue
                raise EarthSearchRemoteError(response.status_code)

            if response.is_error:
                raise EarthSearchRemoteError(response.status_code)

            try:
                decoded = response.json()
            except ValueError as exc:
                raise EarthSearchProtocolError("Earth Search returned invalid JSON.") from exc
            if not isinstance(decoded, Mapping):
                raise EarthSearchProtocolError("Earth Search response must be a JSON object.")
            return decoded


def _validate_polygon(polygon: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(polygon, Mapping):
        raise EarthSearchValidationError("polygon must be a GeoJSON object.")
    if polygon.get("type") != "Polygon":
        raise EarthSearchValidationError("polygon.type must be 'Polygon'.")

    coordinates = polygon.get("coordinates")
    if not _is_sequence(coordinates) or not coordinates:
        raise EarthSearchValidationError("polygon.coordinates must contain at least one ring.")
    if len(coordinates) > 32:
        raise EarthSearchValidationError("polygon cannot contain more than 32 rings.")

    normalized_rings: list[list[list[float]]] = []
    total_vertices = 0
    for ring in coordinates:
        if not _is_sequence(ring) or len(ring) < 4:
            raise EarthSearchValidationError("each polygon ring must contain at least 4 positions.")
        total_vertices += len(ring)
        if total_vertices > MAX_POLYGON_VERTICES:
            raise EarthSearchValidationError(
                f"polygon cannot contain more than {MAX_POLYGON_VERTICES} positions."
            )

        normalized_ring: list[list[float]] = []
        for position in ring:
            if not _is_sequence(position) or len(position) != 2:
                raise EarthSearchValidationError(
                    "each polygon position must contain longitude and latitude."
                )
            longitude = _coordinate(position[0], minimum=-180, maximum=180, name="longitude")
            latitude = _coordinate(position[1], minimum=-90, maximum=90, name="latitude")
            normalized_ring.append([longitude, latitude])

        if normalized_ring[0] != normalized_ring[-1]:
            raise EarthSearchValidationError("each polygon ring must be closed.")
        if len({tuple(position) for position in normalized_ring[:-1]}) < 3:
            raise EarthSearchValidationError("each polygon ring must have 3 distinct vertices.")
        if math.isclose(_twice_ring_area(normalized_ring), 0.0, abs_tol=1e-12):
            raise EarthSearchValidationError("each polygon ring must enclose a non-zero area.")
        normalized_rings.append(normalized_ring)

    return {"type": "Polygon", "coordinates": normalized_rings}


def _validate_bbox(bbox: Sequence[float]) -> list[float]:
    if not _is_sequence(bbox) or len(bbox) != 4:
        raise EarthSearchValidationError("bbox must contain west, south, east, and north.")
    west = _coordinate(bbox[0], minimum=-180, maximum=180, name="west")
    south = _coordinate(bbox[1], minimum=-90, maximum=90, name="south")
    east = _coordinate(bbox[2], minimum=-180, maximum=180, name="east")
    north = _coordinate(bbox[3], minimum=-90, maximum=90, name="north")
    if west >= east:
        raise EarthSearchValidationError("bbox west must be smaller than east.")
    if south >= north:
        raise EarthSearchValidationError("bbox south must be smaller than north.")
    return [west, south, east, north]


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _coordinate(value: object, *, minimum: float, maximum: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EarthSearchValidationError(f"{name} must be numeric.")
    normalized = float(value)
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise EarthSearchValidationError(f"{name} is outside its valid range.")
    return normalized


def _twice_ring_area(ring: Sequence[Sequence[float]]) -> float:
    return sum(
        ring[index][0] * ring[index + 1][1] - ring[index + 1][0] * ring[index][1]
        for index in range(len(ring) - 1)
    )


def _normalize_boundary(value: date | datetime | str, *, is_end: bool) -> datetime:
    if isinstance(value, str):
        stripped = value.strip()
        try:
            if len(stripped) == 10:
                value = date.fromisoformat(stripped)
            else:
                value = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EarthSearchValidationError("dates must use ISO 8601 format.") from exc

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise EarthSearchValidationError("datetime boundaries must include a timezone.")
        return value.astimezone(UTC)
    if isinstance(value, date):
        boundary_time = datetime_time.max if is_end else datetime_time.min
        return datetime.combine(value, boundary_time, tzinfo=UTC)
    raise EarthSearchValidationError("date boundaries must be dates, datetimes, or ISO strings.")


def _validate_interval(start_at: datetime, end_at: datetime) -> None:
    if start_at > end_at:
        raise EarthSearchValidationError("start must not be after end.")
    if end_at - start_at > MAX_SEARCH_INTERVAL:
        raise EarthSearchValidationError("search interval cannot exceed 366 days.")


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise EarthSearchValidationError("limit must be an integer.")
    if not 1 <= limit <= MAX_RESULTS:
        raise EarthSearchValidationError(f"limit must be between 1 and {MAX_RESULTS}.")
    return limit


def _validate_cloud_cover(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EarthSearchValidationError("max_cloud_cover must be numeric.")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0 <= normalized <= 100:
        raise EarthSearchValidationError("max_cloud_cover must be between 0 and 100.")
    return normalized


def _format_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _retry_delay(retry_after: str | None, *, attempt: int, maximum: float) -> float:
    fallback = min(0.25 * (2**attempt), maximum)
    if retry_after is None:
        return fallback
    try:
        seconds = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return fallback
    if not math.isfinite(seconds):
        return fallback
    return min(max(seconds, 0.0), maximum)


def _parse_feature_collection(payload: Mapping[str, Any]) -> tuple[Sentinel2Scene, ...]:
    features = payload.get("features")
    if not _is_sequence(features):
        raise EarthSearchProtocolError("STAC response is missing a features array.")
    return tuple(_parse_scene(feature) for feature in features)


def _parse_scene(feature: object) -> Sentinel2Scene:
    if not isinstance(feature, Mapping):
        raise EarthSearchProtocolError("Each STAC feature must be an object.")

    scene_id = feature.get("id")
    if not isinstance(scene_id, str) or not scene_id.strip():
        raise EarthSearchProtocolError("A STAC feature has no valid id.")

    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid properties.")
    acquired_at = _parse_scene_datetime(properties.get("datetime"), scene_id=scene_id)
    cloud_cover = _parse_scene_cloud_cover(properties.get("eo:cloud_cover"), scene_id=scene_id)

    raw_assets = feature.get("assets")
    if not isinstance(raw_assets, Mapping):
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid assets.")
    normalized_assets = _normalize_assets(raw_assets)
    geometry = _parse_geometry(feature.get("geometry"), scene_id=scene_id)
    bbox = _parse_bbox(feature.get("bbox"), scene_id=scene_id)

    return Sentinel2Scene(
        id=scene_id,
        captured_at=acquired_at,
        cloud_cover=cloud_cover,
        assets=normalized_assets,
        geometry=geometry,
        bbox=bbox,
        properties=dict(properties),
    )


def _parse_geometry(value: object, *, scene_id: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid geometry.")
    geometry_type = value.get("type")
    coordinates = value.get("coordinates")
    if not isinstance(geometry_type, str) or not _is_sequence(coordinates):
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid geometry.")
    return dict(value)


def _parse_bbox(value: object, *, scene_id: str) -> tuple[float, ...] | None:
    if value is None:
        return None
    if not _is_sequence(value) or len(value) not in {4, 6}:
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid bbox.")
    normalized: list[float] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid bbox.")
        numeric_coordinate = float(coordinate)
        if not math.isfinite(numeric_coordinate):
            raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid bbox.")
        normalized.append(numeric_coordinate)
    return tuple(normalized)


def _parse_scene_datetime(value: object, *, scene_id: str) -> datetime:
    if not isinstance(value, str):
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has no datetime.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EarthSearchProtocolError(
            f"STAC feature {scene_id!r} has an invalid datetime."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} datetime has no timezone.")
    return parsed.astimezone(UTC)


def _parse_scene_cloud_cover(value: object, *, scene_id: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid cloud cover.")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0 <= normalized <= 100:
        raise EarthSearchProtocolError(f"STAC feature {scene_id!r} has invalid cloud cover.")
    return normalized


def _normalize_assets(raw_assets: Mapping[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for canonical_name, aliases in _ASSET_ALIASES.items():
        for alias in aliases:
            raw_asset = raw_assets.get(alias)
            if not isinstance(raw_asset, Mapping):
                continue
            href = raw_asset.get("href")
            if _is_public_http_url(href):
                normalized[canonical_name] = href
                break
    return normalized


def _is_public_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )
