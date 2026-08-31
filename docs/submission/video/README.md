# 1415 Agri submission video package

This directory is the source of truth for the English hackathon video. It deliberately contains only
text and vector assets. Raw captures, generated speech, edit projects, and final video files belong in
`C:\Users\giova\Videos\1415-Agri-Hackathon\` and must not be committed.

## Locked delivery specification

- Editorial runtime: **3:53**; acceptable exported runtime: **3:45–3:55**; hard stop: **3:58**.
- Canvas: **1920×1080**, square pixels, **30 fps**.
- Delivery: H.264 video, AAC audio, 48 kHz, no music.
- Narration: English, neutral and measured, approximately 145 words per minute.
- Captions: burned into the final video and delivered separately as `captions_en.srt`.
- Proof: Guided demonstration off; real Sentinel-2 evidence; Gemini 3.5 Flash, Google ADK, MCP,
  agent action, four Cloud Run roles, and one safe execution log visible.
- Privacy: hide trailing coordinate decimals, account email, billing data, credentials, tokens,
  notifications, and unrelated tabs.

## Contents

- `script_timed_en.md` — final editorial timeline, narration, visuals, and contingency rules.
- `shot_list.md` — capture operator checklist with filenames and pass/fail conditions.
- `narration_en.txt` — narration split into independently replaceable TTS or human-voice scenes.
- `captions_en.srt` — valid 3:53 English subtitle track, matching the narration.
- `assets/` — 1920×1080 title, architecture, boundary-control, cloud-proof, closing, and callout SVGs.

## Rendering notes

The title and closing cards reference the canonical logo at
`core/static/core/brand/1415-agri-logo.png`; they do not redraw or reinterpret it. Render the SVGs
with the repository layout intact. Every asset has a 1920×1080 view box and can be rasterized by a
browser, Inkscape, ImageMagick, or an FFmpeg build with SVG support.

Captions use the bottom safe area for the agricultural workflow and move to the top safe area during
the Gemini/action and Cloud proof scenes, keeping tool traces and execution events unobstructed.

## Production order

1. Complete the quota-safe rehearsal and record the observed processing duration.
2. Capture the files in `shot_list.md`, preserving the live workflow as one continuous master.
3. Generate one narration file per scene from `narration_en.txt`.
4. Assemble to the locked timecodes in `script_timed_en.md`.
5. Burn `captions_en.srt`, perform privacy QA, and watch the complete export at normal speed.
