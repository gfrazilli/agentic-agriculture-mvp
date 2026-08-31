"""Generate one neural English voice-over file per timed scene."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path

from video_common import (
    VideoPipelineError,
    load_json,
    media_duration,
    resolve_media_tools,
    safe_scene_id,
)

TOKENS = re.compile(r"[a-z0-9]+")


def _tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return TOKENS.findall(normalized.lower())


def _assert_complete_word_boundaries(path: Path, expected_text: str) -> None:
    spoken: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("type") == "WordBoundary":
            spoken.extend(_tokens(str(item.get("text", ""))))
    expected = _tokens(expected_text)
    if spoken != expected:
        raise VideoPipelineError(
            f"TTS word-boundary stream was incomplete: expected {len(expected)} tokens, "
            f"received {len(spoken)}"
        )


def _scene_duration(scene: dict[str, object]) -> float:
    if "duration" in scene:
        return float(scene["duration"])
    if "duration_seconds" in scene:
        return float(scene["duration_seconds"])
    return float(scene["end"]) - float(scene["start"])


def _signature(*, text: str, voice: str, rate: str, volume: str, pitch: str) -> str:
    payload = json.dumps(
        {
            "text": text,
            "voice": voice,
            "rate": rate,
            "volume": volume,
            "pitch": pitch,
            "boundary": "WordBoundary",
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--voice", default="en-US-GuyNeural")
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--volume", default="+0%")
    parser.add_argument("--pitch", default="+0Hz")
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--headroom",
        type=float,
        default=0.25,
        help="Seconds reserved at the end of each scene.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        import edge_tts
    except ImportError as exc:
        raise VideoPipelineError(
            "edge-tts is missing. Install scripts/video/requirements.txt first."
        ) from exc

    narration = load_json(args.narration.resolve())
    scenes = narration.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise VideoPipelineError("Narration JSON must contain a non-empty scenes array")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "tts_manifest.json"
    prior_report: dict[str, object] = {}
    if report_path.is_file():
        try:
            prior_report = load_json(report_path)
        except VideoPipelineError:
            prior_report = {}
    prior_by_id = {
        item.get("id"): item for item in prior_report.get("scenes", []) if isinstance(item, dict)
    }

    if args.dry_run:
        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                raise VideoPipelineError(f"Scene {index} must be an object")
            scene_id = safe_scene_id(scene.get("id", f"scene_{index:02d}"))
            text = str(scene.get("text", "")).strip()
            duration = _scene_duration(scene)
            if not text or duration <= 0:
                raise VideoPipelineError(f"Scene {scene_id} has invalid text or duration")
            print(f"{scene_id}: {duration:.3f}s, {len(text.split())} words")
        return 0

    _, ffprobe = resolve_media_tools(args.ffmpeg_dir)
    report_scenes: list[dict[str, object]] = []
    overruns: list[str] = []

    for index, scene_value in enumerate(scenes, start=1):
        if not isinstance(scene_value, dict):
            raise VideoPipelineError(f"Scene {index} must be an object")
        scene_id = safe_scene_id(scene_value.get("id", f"scene_{index:02d}"))
        text = str(scene_value.get("text", "")).strip()
        target = _scene_duration(scene_value)
        if not text or target <= 0:
            raise VideoPipelineError(f"Scene {scene_id} has invalid text or duration")

        effective_rate = str(scene_value.get("rate", args.rate))

        signature = _signature(
            text=text,
            voice=args.voice,
            rate=effective_rate,
            volume=args.volume,
            pitch=args.pitch,
        )
        output_path = output_dir / f"{scene_id}.mp3"
        words_path = output_dir / f"{scene_id}.words.jsonl"
        old = prior_by_id.get(scene_id, {})
        reusable = (
            not args.overwrite
            and output_path.is_file()
            and words_path.is_file()
            and isinstance(old, dict)
            and old.get("signature") == signature
        )

        if reusable:
            print(f"Reusing {output_path.name}")
        else:
            temporary_path = output_dir / f".{scene_id}.tmp.mp3"
            temporary_words_path = output_dir / f".{scene_id}.tmp.words.jsonl"
            temporary_path.unlink(missing_ok=True)
            temporary_words_path.unlink(missing_ok=True)
            for attempt in range(1, args.retries + 1):
                try:
                    print(f"Synthesizing {scene_id} ({attempt}/{args.retries})")
                    communication = edge_tts.Communicate(
                        text,
                        args.voice,
                        rate=effective_rate,
                        volume=args.volume,
                        pitch=args.pitch,
                        boundary="WordBoundary",
                    )
                    communication.save_sync(str(temporary_path), str(temporary_words_path))
                    if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
                        raise VideoPipelineError(f"TTS returned an empty file for {scene_id}")
                    if (
                        not temporary_words_path.is_file()
                        or temporary_words_path.stat().st_size == 0
                    ):
                        raise VideoPipelineError(f"TTS returned no word timing for {scene_id}")
                    _assert_complete_word_boundaries(temporary_words_path, text)
                    os.replace(temporary_path, output_path)
                    os.replace(temporary_words_path, words_path)
                    break
                except Exception:
                    temporary_path.unlink(missing_ok=True)
                    temporary_words_path.unlink(missing_ok=True)
                    if attempt >= args.retries:
                        raise
                    time.sleep(2 ** (attempt - 1))

        actual = media_duration(ffprobe, output_path)
        fits = actual <= target - args.headroom
        if not fits:
            overruns.append(
                f"{scene_id}: voice {actual:.3f}s, available {target - args.headroom:.3f}s"
            )
        report_scenes.append(
            {
                "id": scene_id,
                "file": output_path.name,
                "word_timing_file": words_path.name,
                "target_duration": target,
                "audio_duration": actual,
                "headroom": args.headroom,
                "fits": fits,
                "rate": effective_rate,
                "signature": signature,
            }
        )

    report = {
        "version": 1,
        "voice": args.voice,
        "default_rate": args.rate,
        "rate": (
            "per-scene"
            if any("rate" in item for item in scenes if isinstance(item, dict))
            else args.rate
        ),
        "volume": args.volume,
        "pitch": args.pitch,
        "scenes": report_scenes,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")
    if overruns:
        formatted = "\n".join(f"- {item}" for item in overruns)
        raise VideoPipelineError(
            "Narration does not fit its scene. Increase --rate or revise the text:\n" + formatted
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
