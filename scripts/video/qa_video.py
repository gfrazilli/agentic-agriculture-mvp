"""Fail-fast FFprobe and subtitle QA for the final hackathon video."""

from __future__ import annotations

import argparse
import json
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from video_common import VideoPipelineError, probe_json, resolve_media_tools

TIMESTAMP = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)


def _seconds(match: re.Match[str], prefix: str) -> float:
    return (
        int(match[f"{prefix}h"]) * 3600
        + int(match[f"{prefix}m"]) * 60
        + int(match[f"{prefix}s"])
        + int(match[f"{prefix}ms"]) / 1000
    )


def inspect_srt(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    blocks = [block for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]
    if not blocks:
        raise VideoPipelineError(f"Subtitle file is empty: {path}")

    previous_start = -1.0
    previous_end = -1.0
    last_end = 0.0
    max_lines = 0
    max_line_length = 0
    warnings: list[str] = []
    for position, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.splitlines()]
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines:
            raise VideoPipelineError(f"Subtitle block {position} has no timestamp")
        match = TIMESTAMP.match(lines[0].strip())
        if not match:
            raise VideoPipelineError(f"Invalid SRT timestamp in block {position}: {lines[0]}")
        start = _seconds(match, "s")
        end = _seconds(match, "e")
        caption_lines = [line.strip() for line in lines[1:] if line.strip()]
        if not caption_lines:
            raise VideoPipelineError(f"Subtitle block {position} has no text")
        if len(caption_lines) > 2:
            raise VideoPipelineError(
                f"Subtitle block {position} has {len(caption_lines)} lines; maximum is 2"
            )
        if start < previous_start or end <= start:
            raise VideoPipelineError(f"Invalid timing order in subtitle block {position}")
        if start < previous_end - 0.001:
            raise VideoPipelineError(f"Subtitle block {position} overlaps the previous block")
        previous_start = start
        previous_end = end
        last_end = max(last_end, end)
        max_lines = max(max_lines, len(caption_lines))
        for line in caption_lines:
            max_line_length = max(max_line_length, len(line))
            if len(line) > 72:
                warnings.append(f"Subtitle block {position} contains a {len(line)}-character line")

    return {
        "cue_count": len(blocks),
        "last_end_seconds": last_end,
        "max_lines_per_cue": max_lines,
        "max_line_length": max_line_length,
        "warnings": warnings,
    }


def run_qa(
    *,
    video_path: Path,
    captions_path: Path,
    ffprobe: Path,
    expected_duration: float | None = None,
    duration_tolerance: float = 0.30,
    minimum_duration: float | None = None,
    maximum_duration: float = 238.0,
) -> dict[str, Any]:
    if not video_path.is_file() or video_path.stat().st_size == 0:
        raise VideoPipelineError(f"Final video is missing or empty: {video_path}")
    if not captions_path.is_file():
        raise VideoPipelineError(f"Final SRT is missing: {captions_path}")

    probe = probe_json(ffprobe, video_path)
    streams = probe.get("streams", [])
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    if len(video_streams) != 1:
        raise VideoPipelineError(f"Expected one video stream; found {len(video_streams)}")
    if len(audio_streams) != 1:
        raise VideoPipelineError(f"Expected one audio stream; found {len(audio_streams)}")

    video = video_streams[0]
    audio = audio_streams[0]
    duration = float(probe["format"]["duration"])
    fps_text = video.get("avg_frame_rate") or video.get("r_frame_rate")
    fps = float(Fraction(fps_text)) if fps_text and fps_text != "0/0" else 0.0

    failures: list[str] = []
    if (video.get("width"), video.get("height")) != (1920, 1080):
        failures.append(
            f"resolution is {video.get('width')}x{video.get('height')}, expected 1920x1080"
        )
    if video.get("codec_name") != "h264":
        failures.append(f"video codec is {video.get('codec_name')}, expected h264")
    if video.get("pix_fmt") != "yuv420p":
        failures.append(f"pixel format is {video.get('pix_fmt')}, expected yuv420p")
    if abs(fps - 30.0) > 0.01:
        failures.append(f"frame rate is {fps:.6f}, expected 30")
    if audio.get("codec_name") != "aac":
        failures.append(f"audio codec is {audio.get('codec_name')}, expected aac")
    if int(audio.get("sample_rate", 0)) != 48000:
        failures.append(f"audio sample rate is {audio.get('sample_rate')}, expected 48000")
    if minimum_duration is not None and duration < minimum_duration:
        failures.append(f"duration {duration:.3f}s is below minimum {minimum_duration:.3f}s")
    if duration > maximum_duration:
        failures.append(f"duration {duration:.3f}s exceeds maximum {maximum_duration:.3f}s")
    if expected_duration is not None and abs(duration - expected_duration) > duration_tolerance:
        failures.append(
            f"duration {duration:.3f}s differs from expected {expected_duration:.3f}s "
            f"by more than {duration_tolerance:.3f}s"
        )

    subtitles = inspect_srt(captions_path)
    if subtitles["last_end_seconds"] > duration + 0.25:
        failures.append(
            f"last subtitle ends at {subtitles['last_end_seconds']:.3f}s, after video duration"
        )

    report = {
        "status": "failed" if failures else "passed",
        "video": str(video_path),
        "captions": str(captions_path),
        "duration_seconds": duration,
        "resolution": f"{video.get('width')}x{video.get('height')}",
        "frame_rate": fps,
        "video_codec": video.get("codec_name"),
        "pixel_format": video.get("pix_fmt"),
        "audio_codec": audio.get("codec_name"),
        "audio_sample_rate": int(audio.get("sample_rate", 0)),
        "audio_channels": int(audio.get("channels", 0)),
        "subtitle_qa": subtitles,
        "failures": failures,
    }
    if failures:
        raise VideoPipelineError("Final video QA failed:\n- " + "\n- ".join(failures))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--captions", type=Path, required=True)
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--expected-duration", type=float)
    parser.add_argument("--duration-tolerance", type=float, default=0.30)
    parser.add_argument("--minimum-duration", type=float)
    parser.add_argument("--maximum-duration", type=float, default=238.0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    _, ffprobe = resolve_media_tools(args.ffmpeg_dir)
    report = run_qa(
        video_path=args.video.resolve(),
        captions_path=args.captions.resolve(),
        ffprobe=ffprobe,
        expected_duration=args.expected_duration,
        duration_tolerance=args.duration_tolerance,
        minimum_duration=args.minimum_duration,
        maximum_duration=args.maximum_duration,
    )
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.report:
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
