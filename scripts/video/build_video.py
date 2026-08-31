"""Normalize scene captures, add narration, burn captions, and export MP4/SRT."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from qa_video import run_qa
from video_common import (
    VideoPipelineError,
    load_json,
    media_duration,
    resolve_from,
    resolve_media_tools,
    run_command,
    safe_scene_id,
)


def _number(config: dict[str, object], key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError) as exc:
        raise VideoPipelineError(f"Video setting {key!r} must be numeric") from exc


_SRT_START = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->")


def _prepare_burn_captions(source: Path, destination: Path) -> None:
    """Move captions above dense proof panels without altering the exported SRT."""

    top_windows = ((142.0, 195.0), (207.0, 224.0))
    blocks = re.split(r"\r?\n\s*\r?\n", source.read_text(encoding="utf-8-sig").strip())
    rendered: list[str] = []
    for block in blocks:
        lines = block.splitlines()
        timing_index = 1 if lines and lines[0].strip().isdigit() else 0
        if timing_index >= len(lines):
            raise VideoPipelineError("Caption block has no timing line")
        match = _SRT_START.match(lines[timing_index].strip())
        if not match:
            raise VideoPipelineError("Caption block has an invalid timing line")
        hours, minutes, seconds, milliseconds = (int(value) for value in match.groups())
        start = hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
        text_index = timing_index + 1
        if any(lower <= start < upper for lower, upper in top_windows):
            if text_index >= len(lines):
                raise VideoPipelineError("Caption block has no text")
            lines[text_index] = r"{\an8}" + lines[text_index]
        rendered.append("\n".join(lines))
    destination.write_text("\n\n".join(rendered) + "\n", encoding="utf-8")


def _encode_segment(
    *,
    ffmpeg: Path,
    ffprobe: Path,
    clip: Path,
    narration: Path | None,
    output: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
    sample_rate: int,
    crf: int,
    preset: str,
    allow_narration_trim: bool,
) -> None:
    if narration:
        narration_duration = media_duration(ffprobe, narration)
        if narration_duration > duration + 0.05 and not allow_narration_trim:
            raise VideoPipelineError(
                f"Narration {narration.name} is {narration_duration:.3f}s but its scene is only "
                f"{duration:.3f}s. Regenerate it faster or explicitly set "
                '"allow_narration_trim": true.'
            )

    duration_text = f"{duration:.3f}"
    command: list[str | Path] = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", "-i", clip]
    if narration:
        command.extend(["-i", narration])
        audio_filter = (
            f"[1:a]loudnorm=I=-16:LRA=7:TP=-1.5,aresample={sample_rate},"
            f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts=stereo,"
            f"apad=pad_dur={duration_text},atrim=duration={duration_text},"
            "asetpts=PTS-STARTPTS[a]"
        )
    else:
        command.extend(["-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=stereo"])
        audio_filter = f"[1:a]atrim=duration={duration_text},asetpts=PTS-STARTPTS[a]"

    video_filter = (
        f"[0:v]fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0xF8FAF7,"
        "setsar=1,format=yuv420p,"
        f"tpad=stop_mode=clone:stop_duration={duration_text},"
        f"trim=duration={duration_text},setpts=PTS-STARTPTS[v]"
    )
    command.extend(
        [
            "-filter_complex",
            f"{video_filter};{audio_filter}",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-g",
            str(fps * 2),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            str(sample_rate),
            "-t",
            duration_text,
            "-movflags",
            "+faststart",
            output,
        ]
    )
    run_command(command)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-qa", action="store_true")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest_dir = manifest_path.parent
    manifest = load_json(manifest_path)
    if manifest.get("version") != 1:
        raise VideoPipelineError("Only edit manifest version 1 is supported")

    settings = manifest.get("video", {})
    if not isinstance(settings, dict):
        raise VideoPipelineError("Manifest video setting must be an object")
    width = int(_number(settings, "width", 1920))
    height = int(_number(settings, "height", 1080))
    fps = int(_number(settings, "fps", 30))
    sample_rate = int(_number(settings, "audio_sample_rate", 48000))
    segment_crf = int(_number(settings, "segment_crf", 15))
    final_crf = int(_number(settings, "final_crf", 18))
    preset = str(settings.get("preset", "medium"))
    if (width, height, fps, sample_rate) != (1920, 1080, 30, 48000):
        raise VideoPipelineError("Submission contract requires 1920x1080, 30fps, and 48000Hz audio")

    scenes = manifest.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise VideoPipelineError("Manifest must contain a non-empty scenes array")
    captions_value = manifest.get("captions")
    output_value = manifest.get("output")
    if not isinstance(captions_value, str) or not isinstance(output_value, str):
        raise VideoPipelineError("Manifest must define captions and output paths")
    captions = resolve_from(manifest_dir, captions_value)
    output = resolve_from(manifest_dir, output_value)
    caption_output_value = manifest.get("caption_output", str(output.with_suffix(".srt")))
    if not isinstance(caption_output_value, str):
        raise VideoPipelineError("caption_output must be a path string")
    caption_output = resolve_from(manifest_dir, caption_output_value)
    if not captions.is_file():
        raise VideoPipelineError(f"Captions file does not exist: {captions}")
    if output.exists() and not args.overwrite:
        raise VideoPipelineError(
            f"Output already exists: {output}. Pass --overwrite to replace it."
        )

    scene_contracts: list[dict[str, object]] = []
    missing: list[Path] = []
    for index, value in enumerate(scenes, start=1):
        if not isinstance(value, dict):
            raise VideoPipelineError(f"Scene {index} must be an object")
        scene_id = safe_scene_id(value.get("id", f"scene_{index:02d}"))
        duration = float(value.get("duration", 0))
        clip_value = value.get("clip")
        if duration <= 0 or not isinstance(clip_value, str):
            raise VideoPipelineError(f"Scene {scene_id} must define clip and positive duration")
        clip = resolve_from(manifest_dir, clip_value)
        narration_value = value.get("narration")
        narration = (
            resolve_from(manifest_dir, narration_value)
            if isinstance(narration_value, str) and narration_value
            else None
        )
        if not clip.is_file():
            missing.append(clip)
        if narration and not narration.is_file():
            missing.append(narration)
        scene_contracts.append(
            {
                "id": scene_id,
                "duration": duration,
                "clip": clip,
                "narration": narration,
                "allow_narration_trim": bool(value.get("allow_narration_trim", False)),
            }
        )
    if missing:
        raise VideoPipelineError(
            "Required scene files are missing:\n- " + "\n- ".join(str(path) for path in missing)
        )

    ffmpeg, ffprobe = resolve_media_tools(args.ffmpeg_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    caption_output.parent.mkdir(parents=True, exist_ok=True)
    work_dir_value = manifest.get("work_dir", "edit/build")
    if not isinstance(work_dir_value, str):
        raise VideoPipelineError("work_dir must be a path string")
    work_dir = resolve_from(manifest_dir, work_dir_value)
    work_dir.mkdir(parents=True, exist_ok=True)

    segment_paths: list[Path] = []
    for index, scene in enumerate(scene_contracts, start=1):
        segment_path = work_dir / f"segment-{index:02d}-{scene['id']}.mp4"
        print(f"\n[{index}/{len(scene_contracts)}] Encoding {scene['id']}")
        _encode_segment(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            clip=scene["clip"],
            narration=scene["narration"],
            output=segment_path,
            duration=float(scene["duration"]),
            width=width,
            height=height,
            fps=fps,
            sample_rate=sample_rate,
            crf=segment_crf,
            preset=preset,
            allow_narration_trim=bool(scene["allow_narration_trim"]),
        )
        segment_paths.append(segment_path)

    concat_file = work_dir / "segments.txt"
    concat_file.write_text(
        "\n".join(f"file '{path.name.replace(chr(39), chr(39) * 2)}'" for path in segment_paths)
        + "\n",
        encoding="utf-8",
    )
    joined = work_dir / "joined.mov"
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file.name,
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            joined.name,
        ],
        cwd=work_dir,
    )

    captions_for_burn = work_dir / "captions.srt"
    _prepare_burn_captions(captions, captions_for_burn)
    subtitle_filter = (
        "subtitles=filename='captions.srt':"
        "force_style='FontName=Arial,FontSize=16,PrimaryColour=&H00FFFFFF,"
        "BorderStyle=3,BackColour=&H90000000,OutlineColour=&H80000000,"
        "Outline=1,Shadow=0,MarginV=22,Alignment=2'"
    )
    title = str(manifest.get("title", "1415 Agri — All Things Agentic Hackathon"))
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            joined.name,
            "-vf",
            subtitle_filter,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-metadata",
            f"title={title}",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(final_crf),
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-g",
            str(fps * 2),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            str(sample_rate),
            "-movflags",
            "+faststart",
            str(output),
        ],
        cwd=work_dir,
    )
    if captions.resolve() != caption_output.resolve():
        shutil.copy2(captions, caption_output)

    expected_duration = sum(float(scene["duration"]) for scene in scene_contracts)
    if not args.skip_qa:
        report = run_qa(
            video_path=output,
            captions_path=caption_output,
            ffprobe=ffprobe,
            expected_duration=expected_duration,
            duration_tolerance=0.30,
            maximum_duration=238.0,
        )
        report_path = output.with_suffix(".qa.json")
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"QA passed: {report_path}")

    print(f"Final MP4: {output}")
    print(f"Final SRT: {caption_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
