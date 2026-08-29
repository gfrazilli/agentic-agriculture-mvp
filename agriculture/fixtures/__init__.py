"""Stable v1 JSON examples consumed by the UI and contract tests."""

from pathlib import Path

FIXTURE_DIRECTORY = Path(__file__).parent


def fixture_path(name: str) -> Path:
    """Return a path inside this package without reading or mutating it."""

    return FIXTURE_DIRECTORY / name


__all__ = ["FIXTURE_DIRECTORY", "fixture_path"]
