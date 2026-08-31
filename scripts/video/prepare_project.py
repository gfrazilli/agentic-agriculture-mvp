"""Create the external edit manifest from the timed narration contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from video_common import VideoPipelineError, load_json, safe_scene_id


def _scene_duration(scene: dict[str, object]) -> float:
    if "duration" in scene:
        return float(scene["duration"])
    if "duration_seconds" in scene:
        return float(scene["duration_seconds"])
    return float(scene["end"]) - float(scene["start"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--captions", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-name", default="1415-agri-hackathon.mp4")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    narration_path = args.narration.resolve()
    captions_path = args.captions.resolve()
    narration = load_json(narration_path)
    scenes = narration.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise VideoPipelineError("Narration JSON must contain a non-empty scenes array")
    if not captions_path.is_file():
        raise VideoPipelineError(f"Captions file does not exist: {captions_path}")

    workspace = args.workspace.resolve()
    for folder in ("raw", "audio", "edit", "final"):
        (workspace / folder).mkdir(parents=True, exist_ok=True)

    manifest_path = workspace / "edit_manifest.json"
    if manifest_path.exists() and not args.overwrite:
        raise VideoPipelineError(
            f"Manifest already exists: {manifest_path}. Pass --overwrite to replace it."
        )

    edit_scenes: list[dict[str, object]] = []
    for index, scene_value in enumerate(scenes, start=1):
        if not isinstance(scene_value, dict):
            raise VideoPipelineError(f"Scene {index} must be a JSON object")
        scene_id = safe_scene_id(scene_value.get("id", f"scene_{index:02d}"))
        duration = _scene_duration(scene_value)
        if duration <= 0:
            raise VideoPipelineError(f"Scene {scene_id} has a non-positive duration")
        edit_scenes.append(
            {
                "id": scene_id,
                "clip": f"raw/{scene_id}.mp4",
                "narration": f"audio/{scene_id}.mp3",
                "duration": duration,
            }
        )

    manifest = {
        "version": 1,
        "title": "1415 Agri — All Things Agentic Hackathon",
        "video": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "audio_sample_rate": 48000,
            "segment_crf": 15,
            "final_crf": 18,
            "preset": "medium",
        },
        "captions": str(captions_path),
        "output": f"final/{args.output_name}",
        "caption_output": f"final/{Path(args.output_name).stem}.srt",
        "scenes": edit_scenes,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Created {manifest_path}")
    print(f"Expected timeline: {sum(float(item['duration']) for item in edit_scenes):.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
