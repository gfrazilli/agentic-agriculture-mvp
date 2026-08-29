"""Structured geospatial operations exposed through MCP.

The functions in this module deliberately return compact JSON-compatible data.
Pixel arrays stay inside the processing worker instead of being sent through an
LLM context window.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from geospatial.earth_search import EarthSearchClient, Sentinel2Scene
from geospatial.provenance import REQUIRED_BAND_ALIASES, provenance_payload


class InsufficientScenesError(RuntimeError):
    """Raised when a temporal analysis cannot be grounded in two real scenes."""


def _scene_payload(scene: Sentinel2Scene) -> dict[str, Any]:
    payload = scene.to_dict()
    payload["required_bands"] = resolve_required_band_assets(scene.assets)
    return payload


def resolve_required_band_assets(assets: Mapping[str, str]) -> dict[str, str]:
    """Resolve Earth Search asset names to the bands used by this product."""

    resolved: dict[str, str] = {}
    for canonical_name, aliases in REQUIRED_BAND_ALIASES.items():
        for alias in aliases:
            href = assets.get(alias)
            if href:
                resolved[canonical_name] = href
                break
    return resolved


def _evenly_spaced(scenes: Sequence[Sentinel2Scene], count: int) -> tuple[Sentinel2Scene, ...]:
    ordered = tuple(sorted(scenes, key=lambda item: item.captured_at))
    if len(ordered) <= count:
        return ordered
    if count == 1:
        return (ordered[-1],)
    indexes = [round(index * (len(ordered) - 1) / (count - 1)) for index in range(count)]
    return tuple(ordered[index] for index in indexes)


class GeospatialTools:
    """Application service behind the network-facing MCP tools."""

    def __init__(self, client: EarthSearchClient | None = None) -> None:
        self.client = client or EarthSearchClient()

    def search_scenes(
        self,
        *,
        west: float,
        south: float,
        east: float,
        north: float,
        start: date | datetime | str,
        end: date | datetime | str,
        max_cloud_cover: float = 35.0,
        limit: int = 20,
    ) -> dict[str, Any]:
        scenes = self.client.search(
            bbox=(west, south, east, north),
            start=start,
            end=end,
            max_cloud_cover=max_cloud_cover,
            limit=limit,
        )
        return {
            "provenance": provenance_payload(),
            "query": {
                "bbox": [west, south, east, north],
                "start": str(start),
                "end": str(end),
                "max_cloud_cover": max_cloud_cover,
                "limit": limit,
            },
            "scene_count": len(scenes),
            "scenes": [_scene_payload(scene) for scene in scenes],
        }

    def get_scene(self, scene_id: str) -> dict[str, Any]:
        scene = self.client.get_item(scene_id)
        return {
            "provenance": provenance_payload(),
            "scene": _scene_payload(scene),
        }

    def plan_observations(
        self,
        *,
        polygon: Mapping[str, Any],
        start: date | datetime | str,
        end: date | datetime | str,
        max_cloud_cover: float = 35.0,
        scene_count: int = 6,
    ) -> dict[str, Any]:
        if not 2 <= scene_count <= 12:
            raise ValueError("scene_count must be between 2 and 12")
        scenes = self.client.search_scenes(
            polygon=polygon,
            start=start,
            end=end,
            max_cloud_cover=max_cloud_cover,
            limit=min(100, max(scene_count * 4, 20)),
        )
        usable = tuple(
            scene
            for scene in scenes
            if {"B04", "B05", "B08", "B11", "SCL"}
            <= resolve_required_band_assets(scene.assets).keys()
        )
        selected = _evenly_spaced(usable, scene_count)
        if len(selected) < 2:
            raise InsufficientScenesError(
                "At least two cloud-filtered Sentinel-2 scenes with all required bands are needed."
            )
        return {
            "status": "ready",
            "provenance": provenance_payload(),
            "requested_scene_count": scene_count,
            "selected_scene_count": len(selected),
            "selection_method": "chronologically_even_real_observations",
            "scenes": [_scene_payload(scene) for scene in selected],
        }
