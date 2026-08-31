"""End-to-end smoke test with synthetic 1080p clips, audio, and captions."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from video_common import REPO_ROOT, resolve_media_tools, run_command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    ffmpeg, _ = resolve_media_tools(args.ffmpeg_dir)
    smoke_root = Path(tempfile.mkdtemp(prefix="video-smoke-", dir=REPO_ROOT / "tmp")).resolve()
    for folder in ("raw", "audio", "final"):
        (smoke_root / folder).mkdir(parents=True, exist_ok=True)

    try:
        run_command(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=24",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                smoke_root / "raw" / "scene_01.mp4",
            ]
        )
        run_command(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x123C2B:size=720x1280:rate=30",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                smoke_root / "raw" / "scene_02.mp4",
            ]
        )
        for index, frequency in ((1, 440), (2, 554)):
            run_command(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={frequency}:sample_rate=48000",
                    "-t",
                    "1.2",
                    "-c:a",
                    "libmp3lame",
                    smoke_root / "audio" / f"scene_{index:02d}.mp3",
                ]
            )

        captions = smoke_root / "captions_en.srt"
        captions.write_text(
            "1\n00:00:00,100 --> 00:00:01,800\nSynthetic opening scene.\n\n"
            "2\n00:00:02,100 --> 00:00:03,800\nSynthetic architecture scene.\n",
            encoding="utf-8",
        )
        manifest = {
            "version": 1,
            "title": "1415 Agri video pipeline smoke test",
            "video": {
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "audio_sample_rate": 48000,
                "segment_crf": 20,
                "final_crf": 20,
                "preset": "ultrafast",
            },
            "captions": "captions_en.srt",
            "output": "final/smoke.mp4",
            "caption_output": "final/smoke.srt",
            "scenes": [
                {
                    "id": "scene_01",
                    "clip": "raw/scene_01.mp4",
                    "narration": "audio/scene_01.mp3",
                    "duration": 2.0,
                },
                {
                    "id": "scene_02",
                    "clip": "raw/scene_02.mp4",
                    "narration": "audio/scene_02.mp3",
                    "duration": 2.0,
                },
            ],
        }
        manifest_path = smoke_root / "edit_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        command = [
            sys.executable,
            str(Path(__file__).with_name("build_video.py")),
            "--manifest",
            str(manifest_path),
            "--overwrite",
        ]
        if args.ffmpeg_dir:
            command.extend(["--ffmpeg-dir", str(args.ffmpeg_dir.resolve())])
        subprocess.run(command, check=True)
        print(f"Smoke test passed: {smoke_root / 'final' / 'smoke.mp4'}")
        return 0
    finally:
        if args.keep:
            print(f"Kept synthetic assets in {smoke_root}")
        else:
            shutil.rmtree(smoke_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
