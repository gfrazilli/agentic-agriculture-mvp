"""Private MCP service exposing grounded Sentinel-2 tools to Google ADK."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP

from geospatial.tools import GeospatialTools

INSTRUCTIONS = """
Use these tools only to discover and plan real Sentinel-2 L2A observations.
Never infer pests, disease, soil condition, water stress, treatment, or yield.
Scene IDs, timestamps, cloud coverage and asset URLs come from Earth Search.
Pixel arrays are processed by the deterministic backend and are never returned
through the model context.
""".strip()


@lru_cache(maxsize=1)
def get_tools() -> GeospatialTools:
    return GeospatialTools()


mcp = FastMCP(
    "agentic-agriculture-geospatial",
    instructions=INSTRUCTIONS,
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", "8080")),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def search_sentinel_scenes(
    west: float,
    south: float,
    east: float,
    north: float,
    start: str,
    end: str,
    max_cloud_cover: float = 35.0,
    limit: int = 20,
) -> dict[str, Any]:
    """Search real Sentinel-2 L2A scenes for a WGS84 bounding box and date interval."""

    return get_tools().search_scenes(
        west=west,
        south=south,
        east=east,
        north=north,
        start=start,
        end=end,
        max_cloud_cover=max_cloud_cover,
        limit=limit,
    )


@mcp.tool()
def get_sentinel_scene(scene_id: str) -> dict[str, Any]:
    """Fetch one Earth Search item and resolve the exact spectral assets used by the MVP."""

    return get_tools().get_scene(scene_id)


@mcp.tool()
def plan_field_observations(
    polygon: dict[str, Any],
    start: str,
    end: str,
    max_cloud_cover: float = 35.0,
    scene_count: int = 6,
) -> dict[str, Any]:
    """Select chronologically distributed real scenes for one field polygon."""

    return get_tools().plan_observations(
        polygon=polygon,
        start=start,
        end=end,
        max_cloud_cover=max_cloud_cover,
        scene_count=scene_count,
    )


if __name__ == "__main__":  # pragma: no cover - exercised by the container entry point
    mcp.run(transport="streamable-http")
