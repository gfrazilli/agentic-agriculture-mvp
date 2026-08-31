# 1415 Agri video pipeline

This directory contains the local, deterministic post-production pipeline for the
All Things Agentic Hackathon video. It does **not** upload or publish anything.

The pipeline keeps binary media outside Git, normalizes every capture to 1080p/30fps,
adds one narration file per scene, burns English captions, exports a matching SRT, and
fails if FFprobe detects a submission-contract violation.

## Current V2 production package

The approved V2 timeline and source assets live under
`docs/submission/video/v2/`. Binary output defaults to
`%USERPROFILE%\Videos\1415-Agri-Hackathon\v2` and contains a captioned master plus
15 narrated, independently replaceable scene files.

Build all silent scene visuals and then the narrated package:

```powershell
.\.venv\Scripts\python.exe scripts\video\create_raw_scenes_v2.py `
  --overwrite --ffmpeg-dir tmp\video-tools\win32
.\.venv\Scripts\python.exe scripts\video\build_video_v2.py `
  --overwrite --ffmpeg-dir tmp\video-tools\win32
```

After changing only one scene, rebuild that scene and remount the master without
re-encoding the other 14 scene clips:

```powershell
$scene = 'scene_11_farmer_question'
.\.venv\Scripts\python.exe scripts\video\create_raw_scenes_v2.py `
  --scene $scene --overwrite --ffmpeg-dir tmp\video-tools\win32
.\.venv\Scripts\python.exe scripts\video\build_video_v2.py `
  --scene $scene --overwrite --ffmpeg-dir tmp\video-tools\win32
```

The current master is `v2\final\1415-agri-hackathon-v2.mp4`; the individual
clips and their timing/narration index are in `v2\scenes\`.

## One-time setup

Run from the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r scripts\video\requirements.txt
.\.venv\Scripts\python.exe scripts\video\bootstrap.py
```

`bootstrap.py` installs a portable, subtitle-enabled FFmpeg/FFprobe pair under the
Git-ignored `tmp/video-tools/` directory. To use a different pair, pass
`--ffmpeg-dir C:\path\to\bin` or set `VIDEO_FFMPEG_DIR`.

Verify the complete pipeline at any time with synthetic assets:

```powershell
.\.venv\Scripts\python.exe scripts\video\smoke_test.py
```

## Create the external editing workspace

The canonical timed assets are:

- `docs/submission/video/narration.json`
- `docs/submission/video/captions_en.srt`

Create the binary workspace and edit manifest:

```powershell
$videoRoot = 'C:\Users\giova\Videos\1415-Agri-Hackathon'
.\.venv\Scripts\python.exe scripts\video\prepare_project.py `
  --narration docs\submission\video\narration.json `
  --captions docs\submission\video\captions_en.srt `
  --workspace $videoRoot
```

This creates `raw/`, `audio/`, `edit/`, `final/`, and `edit_manifest.json`. Record or
copy each raw capture to the exact `raw/<scene-id>.mp4` path listed in the manifest.
Captures may have different source resolutions or frame rates. A short capture is
extended by holding its final frame; a long capture is trimmed to the declared scene
duration. No clip is accelerated.

For the continuous live-proof scene, record the complete interaction in one raw file.
Do not split it around a loading state, and leave Guided Demonstration off.

## Generate the English neural narration

Validate the narration contract without contacting the speech service:

```powershell
.\.venv\Scripts\python.exe scripts\video\generate_tts.py `
  --narration docs\submission\video\narration.json `
  --output-dir $videoRoot\audio `
  --dry-run
```

Generate the scene blocks with the planned voice:

```powershell
.\.venv\Scripts\python.exe scripts\video\generate_tts.py `
  --narration docs\submission\video\narration.json `
  --output-dir $videoRoot\audio `
  --voice en-US-GuyNeural `
  --rate=+5%
```

The command retries transient failures, stores content signatures, reuses unchanged
audio, measures every MP3 with FFprobe, and fails if a voice block overruns its scene.
The `+5%` rate is the measured setting that fits all 11 approved blocks into the
233-second timeline with end-of-scene headroom. Do not use `allow_narration_trim`
merely to hide an overrun; shorten the narration or regenerate it faster. A human-voice
replacement can use the same per-scene filenames, avoiding any change to the edit
timeline.

Align the approved caption wording to the voice service's measured word boundaries:

```powershell
.\.venv\Scripts\python.exe scripts\video\retime_captions.py `
  --captions docs\submission\video\captions_en.srt `
  --narration docs\submission\video\narration.json `
  --tts-manifest $videoRoot\audio\tts_manifest.json `
  --output $videoRoot\captions_en.srt `
  --edit-manifest $videoRoot\edit_manifest.json
```

This preserves every approved caption line but replaces the planning timestamps with
the actual spoken-word timestamps. The edit manifest is then switched to the
synchronized SRT automatically.

## Build and validate

```powershell
.\.venv\Scripts\python.exe scripts\video\build_video.py `
  --manifest $videoRoot\edit_manifest.json `
  --overwrite
```

The build performs these deterministic stages:

1. Scale with aspect-ratio preservation and pad to 1920x1080.
2. Normalize to 30fps and H.264 High/yuv420p.
3. Normalize narration near -16 LUFS and encode stereo AAC at 48kHz.
4. Concatenate scenes in manifest order.
5. Burn the English SRT with a high-contrast two-line caption style.
6. Export the same captions beside the MP4.
7. Write `<video-name>.qa.json` after the FFprobe and SRT gates pass.

Run the final gate independently:

```powershell
.\.venv\Scripts\python.exe scripts\video\qa_video.py `
  --video $videoRoot\final\1415-agri-hackathon.mp4 `
  --captions $videoRoot\final\1415-agri-hackathon.srt `
  --expected-duration 233 `
  --minimum-duration 225 `
  --maximum-duration 238 `
  --report $videoRoot\final\1415-agri-hackathon.qa.json
```

The gate requires exactly one H.264/yuv420p video stream at 1920x1080 and 30fps,
exactly one AAC 48kHz audio stream, a non-empty SRT with at most two text lines per
cue, ordered timestamps, and a final duration within the configured limits.

## Manual release checks

The automated gate cannot recognize private information in pixels. Before publishing,
watch the full MP4 at normal speed and confirm:

- no full coordinates, account email, billing data, credentials, unrelated tabs, or
  notifications are visible;
- the real Sentinel-2 dates, indices, provenance, Gemini/ADK/MCP trace, state-changing
  action, feedback, Cloud Run roles, and one matching execution log are legible;
- captions are synchronized and do not cover controls or evidence;
- the public upload plays in HD while logged out.

Publishing and Devpost submission remain separate, explicitly authorized actions.
