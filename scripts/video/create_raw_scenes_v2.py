"""Build the fifteen silent, replaceable V2 scene visuals.

The output of this stage is deliberately narration-free. Each file has the exact
scene duration and can be replaced independently before the audio/master stage.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from create_raw_scenes import (
    Shot,
    _chrome_path,
    _encode_still,
    _join_shots,
    _render_svg,
    _validate_scene,
)
from video_common import REPO_ROOT, VideoPipelineError, load_json, resolve_media_tools

DEFAULT_ROOT = Path.home() / "Videos" / "1415-Agri-Hackathon"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=DEFAULT_ROOT / "v2")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--scene",
        action="append",
        help="Rebuild only this scene id; repeat for multiple replaceable cuts.",
    )
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    source_root = args.source_root.resolve()
    narration_path = REPO_ROOT / "docs" / "submission" / "video" / "v2" / "narration.json"
    narration = load_json(narration_path)
    durations = {
        str(scene["id"]): float(scene["duration"])
        for scene in narration.get("scenes", [])
        if isinstance(scene, dict)
    }
    if len(durations) != 15:
        raise VideoPipelineError("V2 narration contract must contain exactly fifteen scenes")

    visual_root = workspace / "visuals"
    build_root = workspace / "edit" / "raw-v2"
    render_root = workspace / "edit" / "rendered-v2"
    visual_root.mkdir(parents=True, exist_ok=True)
    build_root.mkdir(parents=True, exist_ok=True)
    render_root.mkdir(parents=True, exist_ok=True)

    assets = REPO_ROOT / "docs" / "submission" / "video" / "v2" / "assets"
    chrome = _chrome_path()
    profile_root = build_root / "chrome-profile"
    rendered: dict[str, Path] = {}
    for svg in sorted(assets.glob("scene_*.svg")):
        png = render_root / f"{svg.stem}.png"
        _render_svg(chrome, svg, png, profile_root)
        rendered[svg.stem] = png

    capture = source_root / "captures"
    v2_raw = workspace / "raw"

    def cap(name: str) -> Path:
        return capture / name

    def raw(*parts: str) -> Path:
        return v2_raw.joinpath(*parts)

    def card(scene_id: str) -> Path:
        try:
            return rendered[scene_id]
        except KeyError as exc:
            raise VideoPipelineError(f"Missing rendered card for {scene_id}") from exc

    scenes: dict[str, list[Shot]] = {
        "scene_01_human_limit": [Shot(card("scene_01_human_limit"), zoom=1.008)],
        "scene_02_signal_before_symptom": [
            Shot(card("scene_02_signal_before_symptom"), focus_x=0.50, focus_y=0.48, zoom=1.010)
        ],
        "scene_03_workflow_barrier": [
            Shot(card("scene_03_workflow_barrier"), focus_x=0.53, focus_y=0.50, zoom=1.010)
        ],
        "scene_04_product_principle": [Shot(card("scene_04_product_principle"), zoom=1.008)],
        "scene_05_two_click_mapping": [
            Shot(card("scene_05_two_click_mapping"), focus_x=0.53, focus_y=0.48, zoom=1.010)
        ],
        "scene_06_product_reveal": [Shot(card("scene_06_product_reveal"), zoom=1.006)],
        "scene_07_agentic_path": [
            Shot(card("scene_07_agentic_path"), focus_x=0.50, focus_y=0.50, zoom=1.006)
        ],
        "scene_08_field_boundary": [
            Shot(cap("scene03_field_units_36ha.png"), focus_x=0.50, focus_y=0.64, zoom=1.012),
            Shot(
                cap("scene04_boundary_ready.png"),
                crop=(360, 0, 1360, 840),
                focus_x=0.52,
                focus_y=0.62,
                zoom=1.010,
            ),
            Shot(
                cap("scene04_boundary_confirm_button.png"),
                focus_x=0.50,
                focus_y=0.70,
                zoom=1.010,
            ),
        ],
        "scene_09_satellite_workflow": [
            Shot(cap("scene05_processing_00.png"), focus_y=0.58, zoom=1.008),
            Shot(cap("scene05_processing_08.png"), focus_y=0.58, zoom=1.010),
            Shot(cap("scene05_processing_25.png"), focus_y=0.58, zoom=1.010),
            Shot(cap("scene05_processing_46.png"), focus_y=0.58, zoom=1.010),
            Shot(cap("scene05_processing_63.png"), focus_y=0.58, zoom=1.010),
            Shot(cap("scene05_processing_82.png"), focus_y=0.62, zoom=1.010),
        ],
        "scene_10_season_priority": [
            Shot(raw("result", "result-top.png"), focus_x=0.52, focus_y=0.50, zoom=1.010),
            Shot(raw("result", "result-areas.png"), focus_x=0.52, focus_y=0.52, zoom=1.012),
            Shot(raw("result", "result-evidence.png"), focus_x=0.52, focus_y=0.52, zoom=1.012),
        ],
        "scene_11_farmer_question": [
            Shot(card("scene_11_farmer_question"), zoom=1.006),
            Shot(raw("chat", "chat-answer-focused.png"), focus_x=0.72, focus_y=0.48, zoom=1.012),
            Shot(raw("chat", "chat-trace.png"), focus_x=0.72, focus_y=0.55, zoom=1.012),
        ],
        "scene_12_agent_action": [
            Shot(card("scene_12_agent_action"), zoom=1.006),
            Shot(raw("action", "action-prompt.png"), focus_x=0.72, focus_y=0.55, zoom=1.012),
            Shot(raw("action", "action-response.png"), focus_x=0.72, focus_y=0.52, zoom=1.012),
            Shot(raw("chat", "chat-trace.png"), focus_x=0.72, focus_y=0.55, zoom=1.012),
        ],
        "scene_13_feedback": [
            Shot(cap("scene09_feedback_before.png"), focus_x=0.52, focus_y=0.66, zoom=1.010),
            Shot(cap("scene09_feedback_after.png"), focus_x=0.52, focus_y=0.70, zoom=1.012),
            Shot(raw("chat", "chat-trace.png"), focus_x=0.70, focus_y=0.64, zoom=1.012),
        ],
        "scene_14_cloud_proof": [
            Shot(
                raw("cloud", "cloud-run-services.png"),
                crop=(0, 58, 1847, 760),
                focus_x=0.46,
                focus_y=0.42,
                zoom=1.010,
            ),
            Shot(card("scene_14_cloud_proof"), focus_x=0.50, focus_y=0.50, zoom=1.006),
        ],
        "scene_15_close": [Shot(card("scene_15_close"), zoom=1.006)],
    }

    if set(scenes) != set(durations):
        missing = sorted(set(durations) - set(scenes))
        extra = sorted(set(scenes) - set(durations))
        raise VideoPipelineError(f"Scene mapping mismatch; missing={missing}, extra={extra}")

    selected = set(args.scene or scenes)
    unknown = sorted(selected - set(scenes))
    if unknown:
        raise VideoPipelineError(f"Unknown V2 scene ids: {unknown}")

    exact_coordinate_source = cap("scene03_field_units.png").resolve()
    used = {shot.path.resolve() for shots in scenes.values() for shot in shots}
    if exact_coordinate_source in used:
        raise VideoPipelineError("Privacy guard: exact-coordinate capture cannot enter V2")
    missing_files = sorted(str(path) for path in used if not path.is_file())
    if missing_files:
        raise VideoPipelineError("Missing V2 source files:\n- " + "\n- ".join(missing_files))

    ffmpeg, ffprobe = resolve_media_tools(args.ffmpeg_dir)
    report: list[dict[str, object]] = []
    fade = 0.18
    for index, (scene_id, shots) in enumerate(scenes.items(), start=1):
        if scene_id not in selected:
            continue
        duration = durations[scene_id]
        output = visual_root / f"{scene_id}.mp4"
        if output.exists() and not args.overwrite:
            raise VideoPipelineError(f"V2 visual exists: {output}. Pass --overwrite.")
        print(f"\n=== V2 visual [{index:02d}/15] {scene_id} ({duration:.3f}s) ===")
        scene_build = build_root / scene_id
        scene_build.mkdir(parents=True, exist_ok=True)
        overlap = fade * (len(shots) - 1)
        shot_duration = (duration + overlap) / len(shots)
        clips: list[Path] = []
        for shot_index, shot in enumerate(shots, start=1):
            clip = scene_build / f"shot-{shot_index:02d}.mp4"
            _encode_still(ffmpeg=ffmpeg, shot=shot, duration=shot_duration, output=clip)
            clips.append(clip)
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{scene_id}-", suffix=".mp4", dir=visual_root, delete=False
        )
        temporary = Path(handle.name)
        handle.close()
        temporary.unlink()
        try:
            _join_shots(
                ffmpeg=ffmpeg,
                shots=clips,
                shot_duration=shot_duration,
                scene_duration=duration,
                fade_duration=fade,
                output=temporary,
            )
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        report.append(_validate_scene(ffprobe, output, duration))

    report_path = visual_root / "visuals.qa.json"
    prior_by_file: dict[str, dict[str, object]] = {}
    if report_path.is_file():
        prior = load_json(report_path)
        prior_by_file = {
            str(item.get("file")): item
            for item in prior.get("scenes", [])
            if isinstance(item, dict) and item.get("file")
        }
    prior_by_file.update({str(item["file"]): item for item in report})
    ordered_report = [
        prior_by_file[f"{scene_id}.mp4"]
        for scene_id in scenes
        if f"{scene_id}.mp4" in prior_by_file
    ]
    report_path.write_text(
        json.dumps({"status": "passed", "scenes": ordered_report}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"V2 visuals: {visual_root}")
    print(f"QA report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
