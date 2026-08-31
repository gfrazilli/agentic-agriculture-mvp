"""Shared helpers for the deterministic 1415 Agri video pipeline."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]


class VideoPipelineError(RuntimeError):
    """Raised when a video pipeline contract is not satisfied."""


def _executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def resolve_media_tools(explicit_dir: str | Path | None = None) -> tuple[Path, Path]:
    """Resolve a matching FFmpeg/FFprobe pair without mutating PATH."""

    candidates: list[Path] = []
    if explicit_dir:
        candidates.append(Path(explicit_dir).expanduser().resolve())
    if configured := os.environ.get("VIDEO_FFMPEG_DIR"):
        candidates.append(Path(configured).expanduser().resolve())

    ffmpeg_on_path = shutil.which("ffmpeg")
    ffprobe_on_path = shutil.which("ffprobe")
    if ffmpeg_on_path and ffprobe_on_path:
        return Path(ffmpeg_on_path), Path(ffprobe_on_path)

    platform_dirs = ["win32", "linux", "linux64", "darwin"]
    candidates.extend(REPO_ROOT / "tmp" / "video-tools" / name for name in platform_dirs)
    # Compatibility with an early local static-ffmpeg extraction.
    candidates.extend(REPO_ROOT / "tmp" / name for name in platform_dirs)

    try:
        from static_ffmpeg.run import get_platform_dir

        candidates.append(Path(get_platform_dir()))
    except ImportError:
        pass

    ffmpeg_name = _executable_name("ffmpeg")
    ffprobe_name = _executable_name("ffprobe")
    for directory in candidates:
        ffmpeg = directory / ffmpeg_name
        ffprobe = directory / ffprobe_name
        if ffmpeg.is_file() and ffprobe.is_file():
            return ffmpeg, ffprobe

    raise VideoPipelineError(
        f"FFmpeg and FFprobe were not found. Run: {sys.executable} scripts/video/bootstrap.py"
    )


def run_command(
    command: list[str | Path],
    *,
    cwd: str | Path | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command without shell interpolation and fail with useful context."""

    rendered = [str(part) for part in command]
    print("+", subprocess.list2cmdline(rendered), flush=True)
    try:
        return subprocess.run(
            rendered,
            cwd=cwd,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=capture_output,
        )
    except subprocess.CalledProcessError as exc:
        details = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
        raise VideoPipelineError(
            f"Command failed with exit code {exc.returncode}: "
            f"{subprocess.list2cmdline(rendered)}\n{details}"
        ) from exc


def probe_json(ffprobe: Path, media_path: Path) -> dict[str, Any]:
    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            media_path,
        ],
        capture_output=True,
    )
    return json.loads(result.stdout)


def media_duration(ffprobe: Path, media_path: Path) -> float:
    data = probe_json(ffprobe, media_path)
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoPipelineError(f"Could not read duration from {media_path}") from exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoPipelineError(f"Could not read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VideoPipelineError(f"Expected a JSON object in {path}")
    return value


def resolve_from(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def safe_scene_id(value: object) -> str:
    text = str(value).strip().lower()
    normalized = "".join(char if char.isalnum() else "_" for char in text)
    normalized = "_".join(part for part in normalized.split("_") if part)
    if not normalized:
        raise VideoPipelineError(f"Invalid scene id: {value!r}")
    return normalized
