"""Private MCP service exposing grounded Sentinel-2 tools to Google ADK."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from agriculture.observability import audit_event, get_audit_logger
from geospatial.tools import GeospatialTools

INSTRUCTIONS = """
Use these tools only to discover and plan real Sentinel-2 L2A observations.
Never infer pests, disease, soil condition, water stress, treatment, or yield.
Scene IDs, timestamps, cloud coverage and asset URLs come from Earth Search.
Pixel arrays are processed by the deterministic backend and are never returned
through the model context.
""".strip()
logger = get_audit_logger(__name__)


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

    return _invoke_tool(
        "search_sentinel_scenes",
        lambda: get_tools().search_scenes(
            west=west,
            south=south,
            east=east,
            north=north,
            start=start,
            end=end,
            max_cloud_cover=max_cloud_cover,
            limit=limit,
        ),
    )


@mcp.tool()
def get_sentinel_scene(scene_id: str) -> dict[str, Any]:
    """Fetch one Earth Search item and resolve the exact spectral assets used by the MVP."""

    return _invoke_tool("get_sentinel_scene", lambda: get_tools().get_scene(scene_id))


@mcp.tool()
def plan_field_observations(
    polygon: dict[str, Any],
    start: str,
    end: str,
    max_cloud_cover: float = 35.0,
    scene_count: int = 6,
) -> dict[str, Any]:
    """Select chronologically distributed real scenes for one field polygon."""

    return _invoke_tool(
        "plan_field_observations",
        lambda: get_tools().plan_observations(
            polygon=polygon,
            start=start,
            end=end,
            max_cloud_cover=max_cloud_cover,
            scene_count=scene_count,
        ),
    )


def _invoke_tool(tool_name: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Audit MCP calls without serializing arguments, geometry, or provider bodies."""

    execution_id = str(uuid4())
    audit_event(
        logger,
        "mcp.tool.started",
        component="mcp",
        execution_id=execution_id,
        tool_name=tool_name,
        status="started",
    )
    try:
        result = operation()
    except Exception as exc:
        audit_event(
            logger,
            "mcp.tool.failed",
            level=logging.WARNING,
            component="mcp",
            execution_id=execution_id,
            tool_name=tool_name,
            status="failed",
            error_type=type(exc).__name__,
        )
        raise

    scene_ids = _scene_ids(result)
    audit_event(
        logger,
        "mcp.tool.completed",
        component="mcp",
        execution_id=execution_id,
        tool_name=tool_name,
        status="completed",
        scene_count=len(scene_ids),
        scene_ids=scene_ids,
    )
    return result


def _scene_ids(result: dict[str, Any]) -> tuple[str, ...]:
    scenes = result.get("scenes")
    if not isinstance(scenes, list):
        scene = result.get("scene")
        scenes = [scene] if isinstance(scene, dict) else []
    return tuple(
        scene_id
        for scene in scenes
        if isinstance(scene, dict) and isinstance((scene_id := scene.get("id")), str)
    )


if __name__ == "__main__":  # pragma: no cover - exercised by the container entry point
    mcp.run(transport="streamable-http")
