"""Align approved English caption text to Edge TTS word-boundary timings."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from video_common import VideoPipelineError, load_json, safe_scene_id

TIMING = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)
TOKENS = re.compile(r"[a-z0-9]+")


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str]


def _timestamp_seconds(match: re.Match[str], prefix: str) -> float:
    return (
        int(match[f"{prefix}h"]) * 3600
        + int(match[f"{prefix}m"]) * 60
        + int(match[f"{prefix}s"])
        + int(match[f"{prefix}ms"]) / 1000
    )


def _parse_srt(path: Path) -> list[Cue]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cues: list[Cue] = []
    for position, block in enumerate(re.split(r"\n\s*\n", text.strip()), start=1):
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines:
            raise VideoPipelineError(f"Caption block {position} is empty")
        match = TIMING.match(lines[0].strip())
        if not match:
            raise VideoPipelineError(f"Caption block {position} has an invalid timestamp")
        text_lines = [line.rstrip() for line in lines[1:] if line.strip()]
        if not text_lines or len(text_lines) > 2:
            raise VideoPipelineError(f"Caption block {position} must contain one or two lines")
        cues.append(
            Cue(
                start=_timestamp_seconds(match, "s"),
                end=_timestamp_seconds(match, "e"),
                lines=text_lines,
            )
        )
    return cues


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return TOKENS.findall(normalized.lower())


def _word_boundaries(path: Path) -> list[dict[str, object]]:
    boundaries: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("type") == "WordBoundary":
            boundaries.append(item)
    if not boundaries:
        raise VideoPipelineError(f"No word boundaries found in {path}")
    return boundaries


def _format_timestamp(value: float) -> str:
    total_ms = max(0, round(value * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--captions", type=Path, required=True)
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--tts-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--edit-manifest",
        type=Path,
        help="Optionally point an existing edit manifest at the synchronized SRT.",
    )
    args = parser.parse_args()

    source_cues = _parse_srt(args.captions.resolve())
    narration = load_json(args.narration.resolve())
    tts_manifest_path = args.tts_manifest.resolve()
    tts_manifest = load_json(tts_manifest_path)
    tts_dir = tts_manifest_path.parent
    report_by_id = {
        item.get("id"): item for item in tts_manifest.get("scenes", []) if isinstance(item, dict)
    }

    output_cues: list[Cue] = []
    for index, scene_value in enumerate(narration.get("scenes", []), start=1):
        if not isinstance(scene_value, dict):
            raise VideoPipelineError(f"Narration scene {index} must be an object")
        scene_id = safe_scene_id(scene_value.get("id", f"scene_{index:02d}"))
        scene_start = float(scene_value["start"])
        scene_end = float(scene_value["end"])
        scene_cues = [cue for cue in source_cues if scene_start <= cue.start < scene_end - 0.001]
        if not scene_cues:
            raise VideoPipelineError(f"No approved captions found for {scene_id}")
        report = report_by_id.get(scene_id)
        if not isinstance(report, dict):
            raise VideoPipelineError(f"No TTS report found for {scene_id}")
        timing_name = report.get("word_timing_file")
        if not isinstance(timing_name, str):
            raise VideoPipelineError(f"No word timing file recorded for {scene_id}")
        boundaries = _word_boundaries(tts_dir / timing_name)

        approved_tokens = _tokens(" ".join(" ".join(cue.lines) for cue in scene_cues))
        spoken_tokens: list[str] = []
        token_to_boundary: list[int] = []
        for boundary_index, boundary in enumerate(boundaries):
            boundary_tokens = _tokens(str(boundary.get("text", "")))
            spoken_tokens.extend(boundary_tokens)
            token_to_boundary.extend([boundary_index] * len(boundary_tokens))
        if approved_tokens != spoken_tokens:
            raise VideoPipelineError(
                f"Approved caption text and spoken word timing differ for {scene_id}"
            )

        raw_cues: list[Cue] = []
        token_cursor = 0
        for cue in scene_cues:
            cue_token_count = len(_tokens(" ".join(cue.lines)))
            first_boundary = boundaries[token_to_boundary[token_cursor]]
            token_cursor += cue_token_count
            last_boundary = boundaries[token_to_boundary[token_cursor - 1]]
            start = scene_start + float(first_boundary["offset"]) / 10_000_000
            end = (
                scene_start
                + (float(last_boundary["offset"]) + float(last_boundary["duration"])) / 10_000_000
            )
            raw_cues.append(Cue(start=start, end=end, lines=cue.lines))

        audio_duration = float(report["audio_duration"])
        for cue_index, cue in enumerate(raw_cues):
            if cue_index + 1 < len(raw_cues):
                end = raw_cues[cue_index + 1].start - 0.04
            else:
                end = min(scene_end, max(cue.end + 0.12, scene_start + audio_duration + 0.08))
            if end <= cue.start:
                raise VideoPipelineError(f"Word timings overlap inside {scene_id}")
            output_cues.append(Cue(start=cue.start, end=end, lines=cue.lines))

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for index, cue in enumerate(output_cues, start=1):
        blocks.append(
            f"{index}\n{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}\n"
            + "\n".join(cue.lines)
        )
    output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    print(f"Wrote {len(output_cues)} synchronized cues to {output_path}")
    if args.edit_manifest:
        edit_manifest_path = args.edit_manifest.resolve()
        edit_manifest = load_json(edit_manifest_path)
        edit_manifest["captions"] = str(output_path)
        edit_manifest_path.write_text(json.dumps(edit_manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {edit_manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
