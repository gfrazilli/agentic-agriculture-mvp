"""Install the portable FFmpeg/FFprobe pair used by the video pipeline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from video_common import REPO_ROOT, VideoPipelineError, run_command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--download-root",
        type=Path,
        default=REPO_ROOT / "tmp" / "video-tools",
        help="Ignored directory where portable binaries are stored.",
    )
    args = parser.parse_args()

    try:
        import static_ffmpeg
        from static_ffmpeg.run import get_platform_dir
    except ImportError as exc:
        raise VideoPipelineError(
            "Install the isolated video requirements first: "
            f"{sys.executable} -m pip install -r scripts/video/requirements.txt"
        ) from exc

    platform_name = Path(get_platform_dir()).name
    executable_dir = args.download_root.resolve() / platform_name
    executable_dir.mkdir(parents=True, exist_ok=True)
    static_ffmpeg.add_paths(download_dir=str(executable_dir))

    suffix = ".exe" if sys.platform == "win32" else ""
    ffmpeg = executable_dir / f"ffmpeg{suffix}"
    ffprobe = executable_dir / f"ffprobe{suffix}"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        raise VideoPipelineError(f"Portable media tools were not installed in {executable_dir}")

    run_command([ffmpeg, "-hide_banner", "-version"], capture_output=False)
    filters = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-filters"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    if " subtitles " not in filters:
        raise VideoPipelineError("This FFmpeg build does not include the subtitles/libass filter")
    run_command([ffprobe, "-hide_banner", "-version"], capture_output=False)
    print(f"VIDEO_FFMPEG_DIR={executable_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
