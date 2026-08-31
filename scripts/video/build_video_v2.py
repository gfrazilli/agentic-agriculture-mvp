"""Attach V2 narration to replaceable scenes and build the captioned master."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from build_video import _encode_segment
from qa_video import run_qa
from video_common import (
    REPO_ROOT,
    VideoPipelineError,
    load_json,
    media_duration,
    probe_json,
    resolve_media_tools,
    run_command,
)

DEFAULT_WORKSPACE = Path.home() / "Videos" / "1415-Agri-Hackathon" / "v2"

SRT_START = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->")


def _prepare_burn_captions(source: Path, destination: Path) -> None:
    """Keep subtitles away from the dense evidence panels in scenes 12–14.

    Scene 11 keeps captions at the bottom: its large question occupies the top of
    the frame, while the small editorial footer is the safer element to cover.
    """

    blocks = re.split(r"\r?\n\s*\r?\n", source.read_text(encoding="utf-8-sig").strip())
    rendered: list[str] = []
    for block in blocks:
        lines = block.splitlines()
        timing_index = 1 if lines and lines[0].strip().isdigit() else 0
        match = SRT_START.match(lines[timing_index].strip())
        if not match:
            raise VideoPipelineError(f"Invalid caption timing: {lines[timing_index]}")
        hours, minutes, seconds, milliseconds = (int(value) for value in match.groups())
        start = hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
        text_index = timing_index + 1
        if 175.0 <= start < 228.0:
            lines[text_index] = r"{\an8}" + lines[text_index]
        rendered.append("\n".join(lines))
    destination.write_text("\n\n".join(rendered) + "\n", encoding="utf-8")


def _validate_scene_media(ffprobe: Path, path: Path, expected: float) -> dict[str, object]:
    probe = probe_json(ffprobe, path)
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})
    duration = float(probe["format"]["duration"])
    failures: list[str] = []
    if (video.get("width"), video.get("height")) != (1920, 1080):
        failures.append("resolution")
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        failures.append("video codec")
    if audio.get("codec_name") != "aac" or int(audio.get("sample_rate", 0)) != 48000:
        failures.append("audio codec")
    if abs(duration - expected) > 0.08:
        failures.append(f"duration {duration:.3f}s")
    if failures:
        raise VideoPipelineError(f"{path.name}: invalid {', '.join(failures)}")
    return {"file": path.name, "duration": duration, "status": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--scene",
        action="append",
        help="Rebuild only this narrated scene clip, then remount the master.",
    )
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    visuals = workspace / "visuals"
    audio = workspace / "audio"
    scenes_dir = workspace / "scenes"
    final_dir = workspace / "final"
    build_dir = workspace / "edit" / "master-v2"
    captions = REPO_ROOT / "docs" / "submission" / "video" / "v2" / "captions_en.srt"
    narration_path = REPO_ROOT / "docs" / "submission" / "video" / "v2" / "narration.json"
    narration = load_json(narration_path)
    scenes = narration.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 15:
        raise VideoPipelineError("V2 narration contract must contain exactly fifteen scenes")
    scene_ids = {str(scene["id"]) for scene in scenes if isinstance(scene, dict)}
    selected = set(args.scene or scene_ids)
    unknown = sorted(selected - scene_ids)
    if unknown:
        raise VideoPipelineError(f"Unknown V2 scene ids: {unknown}")
    if not captions.is_file():
        raise VideoPipelineError(f"Retimed V2 captions are missing: {captions}")

    scenes_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe = resolve_media_tools(args.ffmpeg_dir)

    rendered_scenes: list[Path] = []
    index: list[dict[str, object]] = []
    qa_scenes: list[dict[str, object]] = []
    for position, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            raise VideoPipelineError(f"Scene {position} is not an object")
        scene_id = str(scene["id"])
        duration = float(scene["duration"])
        visual = visuals / f"{scene_id}.mp4"
        narration_audio = audio / f"{scene_id}.mp3"
        output = scenes_dir / f"{position:02d}_{scene_id}.mp4"
        missing = [str(path) for path in (visual, narration_audio) if not path.is_file()]
        if missing:
            raise VideoPipelineError("Missing V2 scene inputs:\n- " + "\n- ".join(missing))
        rebuild = scene_id in selected
        if output.exists() and not rebuild:
            qa_scenes.append(_validate_scene_media(ffprobe, output, duration))
            rendered_scenes.append(output)
            index.append(
                {
                    "number": position,
                    "id": scene_id,
                    "start": float(scene["start"]),
                    "end": float(scene["end"]),
                    "duration": duration,
                    "video": output.name,
                    "narration": str(scene["text"]),
                }
            )
            continue
        if output.exists() and not args.overwrite:
            raise VideoPipelineError(f"Scene already exists: {output}. Pass --overwrite.")
        audio_duration = media_duration(ffprobe, narration_audio)
        if audio_duration > duration + 0.05:
            raise VideoPipelineError(
                f"{scene_id}: narration {audio_duration:.3f}s exceeds {duration:.3f}s window"
            )
        print(f"\n=== Narrated scene [{position:02d}/15] {scene_id} ===")
        _encode_segment(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            clip=visual,
            narration=narration_audio,
            output=output,
            duration=duration,
            width=1920,
            height=1080,
            fps=30,
            sample_rate=48000,
            crf=15,
            preset="fast",
            allow_narration_trim=False,
        )
        qa_scenes.append(_validate_scene_media(ffprobe, output, duration))
        rendered_scenes.append(output)
        index.append(
            {
                "number": position,
                "id": scene_id,
                "start": float(scene["start"]),
                "end": float(scene["end"]),
                "duration": duration,
                "video": output.name,
                "narration": str(scene["text"]),
            }
        )

    (scenes_dir / "index.json").write_text(
        json.dumps({"version": 2, "scenes": index}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    concat = build_dir / "scenes.txt"
    concat_entries = (
        f"file '{path.as_posix().replace(chr(39), chr(39) * 2)}'" for path in rendered_scenes
    )
    concat.write_text(
        "\n".join(concat_entries) + "\n",
        encoding="utf-8",
    )
    joined = build_dir / "joined-v2.mp4"
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
            concat,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c",
            "copy",
            joined,
        ]
    )

    burn_srt = build_dir / "captions-v2.srt"
    _prepare_burn_captions(captions, burn_srt)
    final = final_dir / "1415-agri-hackathon-v2.mp4"
    final_srt = final_dir / "1415-agri-hackathon-v2.srt"
    if final.exists() and not args.overwrite:
        raise VideoPipelineError(f"Master already exists: {final}. Pass --overwrite.")
    subtitle_filter = (
        "subtitles=filename='captions-v2.srt':"
        "force_style='FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,"
        "BorderStyle=3,BackColour=&H9A071C15,OutlineColour=&H80000000,"
        "Outline=1,Shadow=0,MarginV=24,Alignment=2'"
    )
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
            "-metadata",
            "title=1415 Agri — All Things Agentic Hackathon",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-g",
            "60",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            final,
        ],
        cwd=build_dir,
    )
    shutil.copy2(captions, final_srt)

    expected = sum(float(scene["duration"]) for scene in scenes if isinstance(scene, dict))
    final_qa = run_qa(
        video_path=final,
        captions_path=final_srt,
        ffprobe=ffprobe,
        expected_duration=expected,
        duration_tolerance=0.30,
        minimum_duration=230.0,
        maximum_duration=238.0,
    )
    report = {
        "status": "passed",
        "master": final_qa,
        "replaceable_scenes": qa_scenes,
        "scene_count": len(rendered_scenes),
    }
    report_path = final_dir / "1415-agri-hackathon-v2.qa.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nMaster: {final}")
    print(f"Captions: {final_srt}")
    print(f"Replaceable scene videos: {scenes_dir}")
    print(f"QA: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
