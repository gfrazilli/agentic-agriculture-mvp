"""Create the eleven silent raw scene clips from approved screenshots and SVG cards."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from video_common import REPO_ROOT, VideoPipelineError, load_json, probe_json, resolve_media_tools


@dataclass(frozen=True)
class Shot:
    path: Path
    crop: tuple[int, int, int, int] | None = None  # x, y, width, height
    focus_x: float = 0.5
    focus_y: float = 0.5
    zoom: float = 1.022


def _chrome_path() -> Path:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise VideoPipelineError("Chrome or Edge is required to render the local SVG cards")


def _render_svg(chrome: Path, svg: Path, output: Path, profile_root: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(chrome),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--allow-file-access-from-files",
        "--no-first-run",
        f"--user-data-dir={profile_root}",
        "--window-size=1920,1080",
        "--force-device-scale-factor=1",
        f"--screenshot={output}",
        svg.resolve().as_uri(),
    ]
    print("+", subprocess.list2cmdline(command), flush=True)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0 or not output.is_file():
        raise VideoPipelineError(f"Could not render {svg}\n{result.stdout}\n{result.stderr}")


def _encode_still(
    *,
    ffmpeg: Path,
    shot: Shot,
    duration: float,
    output: Path,
    fps: int = 30,
) -> None:
    filters: list[str] = []
    if shot.crop:
        x, y, width, height = shot.crop
        filters.append(f"crop={width}:{height}:{x}:{y}")
    filters.extend(
        [
            "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos",
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0xF4F8F5",
        ]
    )
    frame_count = max(1, round(duration * fps))
    zoom_step = max(0.000001, (shot.zoom - 1.0) / frame_count)
    filters.append(
        "zoompan="
        f"z='min(max(zoom,pzoom)+{zoom_step:.8f},{shot.zoom:.6f})':"
        f"x='(iw-iw/zoom)*{shot.focus_x:.4f}':"
        f"y='(ih-ih/zoom)*{shot.focus_y:.4f}':"
        "d=1:s=1920x1080:fps=30"
    )
    filters.extend(
        [
            "scale=iw:ih:out_range=tv",
            "format=yuv420p",
            "setparams=range=tv",
        ]
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        shot.path,
        "-vf",
        ",".join(filters),
        "-an",
        "-t",
        f"{duration:.6f}",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "12",
        "-profile:v",
        "high",
        "-level",
        "4.2",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-r",
        str(fps),
        "-g",
        str(fps * 2),
        "-movflags",
        "+faststart",
        output,
    ]
    rendered = [str(item) for item in command]
    print("+", subprocess.list2cmdline(rendered), flush=True)
    subprocess.run(rendered, check=True)


def _join_shots(
    *,
    ffmpeg: Path,
    shots: list[Path],
    shot_duration: float,
    scene_duration: float,
    fade_duration: float,
    output: Path,
) -> None:
    if len(shots) == 1:
        shutil.copyfile(shots[0], output)
        return

    command: list[str | Path] = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]
    for shot in shots:
        command.extend(["-i", shot])

    filter_parts = [
        f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS[v{index}]" for index in range(len(shots))
    ]
    previous = "v0"
    for index in range(1, len(shots)):
        output_label = f"x{index}"
        offset = index * (shot_duration - fade_duration)
        filter_parts.append(
            f"[{previous}][v{index}]xfade=transition=fade:duration={fade_duration:.3f}:"
            f"offset={offset:.6f}[{output_label}]"
        )
        previous = output_label

    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts) + f";[{previous}]scale=iw:ih:out_range=tv,format=yuv420p,"
            f"setparams=range=tv[out]",
            "-map",
            "[out]",
            "-an",
            "-t",
            f"{scene_duration:.6f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "12",
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-r",
            "30",
            "-g",
            "60",
            "-movflags",
            "+faststart",
            output,
        ]
    )
    rendered = [str(item) for item in command]
    print("+", subprocess.list2cmdline(rendered), flush=True)
    subprocess.run(rendered, check=True)


def _validate_scene(ffprobe: Path, path: Path, expected_duration: float) -> dict[str, object]:
    data = probe_json(ffprobe, path)
    video_streams = [
        stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"
    ]
    if len(video_streams) != 1:
        raise VideoPipelineError(f"{path.name}: expected one video stream")
    stream = video_streams[0]
    duration = float(data["format"]["duration"])
    failures = []
    if (stream.get("width"), stream.get("height")) != (1920, 1080):
        failures.append("resolution")
    if stream.get("codec_name") != "h264":
        failures.append("codec")
    if stream.get("pix_fmt") != "yuv420p":
        failures.append("pixel format")
    if stream.get("avg_frame_rate") != "30/1":
        failures.append("frame rate")
    if abs(duration - expected_duration) > 0.05:
        failures.append(f"duration {duration:.3f}s")
    if failures:
        raise VideoPipelineError(f"{path.name}: invalid {', '.join(failures)}")
    return {"file": path.name, "duration": duration, "status": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(r"C:\Users\giova\Videos\1415-Agri-Hackathon"),
    )
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    capture_root = workspace / "captures"
    raw_root = workspace / "raw"
    build_root = workspace / "edit" / "raw-builder"
    manifest_path = workspace / "edit_manifest.json"
    if not capture_root.is_dir() or not manifest_path.is_file():
        raise VideoPipelineError("The workspace must contain captures/ and edit_manifest.json")
    raw_root.mkdir(parents=True, exist_ok=True)
    build_root.mkdir(parents=True, exist_ok=True)

    manifest = load_json(manifest_path)
    durations = {
        str(scene["id"]): float(scene["duration"])
        for scene in manifest.get("scenes", [])
        if isinstance(scene, dict)
    }
    required_scene_ids = {
        f"scene_{index:02d}_{suffix}"
        for index, suffix in [
            (1, "problem"),
            (2, "architecture"),
            (3, "field_context"),
            (4, "boundary"),
            (5, "processing"),
            (6, "results"),
            (7, "gemini"),
            (8, "agent_action"),
            (9, "feedback"),
            (10, "cloud"),
            (11, "close"),
        ]
    }
    if set(durations) != required_scene_ids:
        raise VideoPipelineError("The edit manifest does not contain the expected eleven scenes")

    assets = REPO_ROOT / "docs" / "submission" / "video" / "assets"
    render_root = build_root / "rendered-cards"
    render_root.mkdir(parents=True, exist_ok=True)
    chrome = _chrome_path()
    profile_root = build_root / "chrome-profile"
    cards = {
        "architecture": assets / "02_architecture_1920x1080.svg",
        "field_context": assets / "scene_03_field_context.svg",
        "boundary_control": assets / "scene_04_boundary_control.svg",
        "cloud": assets / "scene_10_cloud_verified.svg",
        "close": assets / "03_closing_1920x1080.svg",
    }
    rendered_cards: dict[str, Path] = {}
    for name, svg in cards.items():
        png = render_root / f"{name}.png"
        _render_svg(chrome, svg, png, profile_root)
        rendered_cards[name] = png

    def cap(name: str) -> Path:
        return capture_root / name

    scene_shots: dict[str, list[Shot]] = {
        "scene_01_problem": [
            Shot(cap("scene01_hero.png"), focus_x=0.46, focus_y=0.42, zoom=1.018),
            Shot(cap("scene01_spectra.png"), focus_x=0.50, focus_y=0.55, zoom=1.022),
        ],
        "scene_02_architecture": [Shot(rendered_cards["architecture"], zoom=1.015)],
        "scene_03_field_context": [
            Shot(rendered_cards["field_context"], zoom=1.012),
            Shot(cap("scene03_field_filled.png"), focus_x=0.48, focus_y=0.40, zoom=1.022),
        ],
        "scene_04_boundary": [
            Shot(
                cap("scene04_boundary_ready.png"),
                crop=(455, 0, 1260, 650),
                focus_y=0.48,
                zoom=1.018,
            ),
            Shot(rendered_cards["boundary_control"], focus_y=0.52, zoom=1.014),
        ],
        "scene_05_processing": [
            Shot(cap("scene05_processing_00.png"), zoom=1.018),
            Shot(cap("scene05_processing_08.png"), focus_y=0.58, zoom=1.020),
            Shot(cap("scene05_processing_25.png"), focus_y=0.58, zoom=1.020),
            Shot(cap("scene05_processing_46.png"), focus_y=0.58, zoom=1.020),
            Shot(cap("scene05_processing_63.png"), focus_y=0.58, zoom=1.020),
            Shot(cap("scene05_processing_82.png"), focus_y=0.60, zoom=1.020),
        ],
        "scene_06_results": [
            Shot(cap("scene06_result_top.png"), focus_y=0.46, zoom=1.020),
            Shot(cap("scene06_result_areas.png"), focus_y=0.55, zoom=1.022),
            Shot(cap("scene06_result_evidence.png"), focus_y=0.52, zoom=1.022),
        ],
        "scene_07_gemini": [
            Shot(
                cap("scene07_gemini_answer_evidence2.png"),
                crop=(1190, 220, 560, 754),
                focus_y=0.35,
                zoom=1.020,
            ),
            Shot(
                cap("scene07_chat_focus.png"),
                crop=(1050, 0, 720, 974),
                focus_y=0.55,
                zoom=1.020,
            ),
            Shot(cap("scene07_chat_lower_page.png"), focus_x=0.62, zoom=1.018),
        ],
        "scene_08_agent_action": [
            Shot(cap("scene08_action_prompt.png"), focus_x=0.60, zoom=1.018),
            Shot(cap("scene08_action_thinking.png"), focus_x=0.60, zoom=1.018),
            Shot(cap("scene08_action_response_22.png"), focus_x=0.60, zoom=1.018),
            Shot(
                cap("scene08_action_response_22.png"),
                crop=(1050, 0, 720, 974),
                focus_y=0.45,
                zoom=1.020,
            ),
        ],
        "scene_09_feedback": [
            Shot(cap("scene09_feedback_before.png"), focus_y=0.65, zoom=1.018),
            Shot(cap("scene09_feedback_after.png"), focus_y=0.68, zoom=1.020),
            Shot(
                cap("scene09_feedback_after.png"),
                crop=(430, 430, 930, 544),
                focus_x=0.42,
                focus_y=0.66,
                zoom=1.018,
            ),
        ],
        "scene_10_cloud": [
            Shot(
                cap("scene10_cloud_services.png"),
                crop=(270, 60, 675, 460),
                focus_x=0.50,
                focus_y=0.48,
                zoom=1.018,
            ),
            Shot(rendered_cards["cloud"], focus_y=0.50, zoom=1.014),
        ],
        "scene_11_close": [Shot(rendered_cards["close"], zoom=1.012)],
    }

    used_files = {shot.path.resolve() for shots in scene_shots.values() for shot in shots}
    prohibited = cap("scene03_field_units.png").resolve()
    if prohibited in used_files:
        raise VideoPipelineError("Privacy guard: exact-coordinate screenshot cannot be used")
    missing = sorted(str(path) for path in used_files if not path.is_file())
    if missing:
        raise VideoPipelineError("Missing scene source files:\n- " + "\n- ".join(missing))

    ffmpeg, ffprobe = resolve_media_tools(args.ffmpeg_dir)
    report: list[dict[str, object]] = []
    # Keep transitions crisp so text-heavy proof screens never become ghosted or unreadable.
    fade_duration = 0.20
    for scene_index, (scene_id, shots) in enumerate(scene_shots.items(), start=1):
        duration = durations[scene_id]
        output = raw_root / f"{scene_id}.mp4"
        if output.exists() and not args.overwrite:
            raise VideoPipelineError(f"Raw scene exists: {output}. Pass --overwrite.")
        print(f"\n=== [{scene_index}/11] {scene_id} ({duration:.3f}s) ===")
        scene_build = build_root / scene_id
        scene_build.mkdir(parents=True, exist_ok=True)
        overlap = fade_duration * (len(shots) - 1)
        shot_duration = (duration + overlap) / len(shots)
        shot_clips: list[Path] = []
        for shot_index, shot in enumerate(shots, start=1):
            shot_output = scene_build / f"shot-{shot_index:02d}.mp4"
            _encode_still(
                ffmpeg=ffmpeg,
                shot=shot,
                duration=shot_duration,
                output=shot_output,
            )
            shot_clips.append(shot_output)
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{scene_id}-", suffix=".mp4", dir=raw_root, delete=False
        )
        temporary_output = Path(handle.name)
        handle.close()
        temporary_output.unlink()
        try:
            _join_shots(
                ffmpeg=ffmpeg,
                shots=shot_clips,
                shot_duration=shot_duration,
                scene_duration=duration,
                fade_duration=fade_duration,
                output=temporary_output,
            )
            temporary_output.replace(output)
        finally:
            temporary_output.unlink(missing_ok=True)
        report.append(_validate_scene(ffprobe, output, duration))

    report_path = raw_root / "raw-scenes.qa.json"
    report_path.write_text(
        json.dumps({"status": "passed", "scenes": report}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Created and validated all raw scenes: {raw_root}")
    print(f"QA report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
