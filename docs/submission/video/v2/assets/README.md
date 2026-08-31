# V2 keynote assets

Eight original, editable SVG master cards support Scenes 1–7 and 15 of
`docs/submission/video/script_v2_proposed.md`. Every SVG uses a 1920×1080 view box, safe margins,
high-contrast typography and named groups for simple reveal animation. The visual language is
minimal and editorial: off-white, deep 1415 Agri green, restrained Google-product accent colors and
no synthetic “AI glow”.

## Scene files

| Scene | File | Primary animation handles |
|---|---|---|
| 1 | `scene_01_human_limit.svg` | `#visible-field-layer`, `#infrared-field-layer`, `#infrared-wipe`, `#copy-line-1`, `#copy-line-2` |
| 2 | `scene_02_signal_before_symptom.svg` | `#day-46-card`, `#plus-18-days`, `#day-64-card`, `#citation` |
| 3 | `scene_03_workflow_barrier.svg` | `#step-1` through `#step-5`, `#convergence`, `#gemini-conversation`, `#bottom-claim` |
| 4 | `scene_04_product_principle.svg` | `#principle-line-1` through `#principle-line-3` |
| 5 | `scene_05_two_click_mapping.svg` | `#legacy-gps`, `#reference-point`, `#confirmed-boundary`, `#mapping-labels` |
| 6 | `scene_06_product_reveal.svg` | `#canonical-logo`, `#product-positioning`, `#proof-transition` |
| 7 | `scene_07_agentic_path.svg` | `#reasoning-path`, its `#node-*` children, `#data-path`, `#stack-footer` |
| 15 | `scene_15_close.svg` | `#closing-principle`, `#closing-logo`, `#closing-url` |

## Editorial use

- Scene 1 is an **original aerial-style vector composition**, not satellite evidence. For the final
  edit, place the authorized real field/Sentinel footage underneath the text and use this card as the
  animation/layout reference. Never present the vector background as a real observation.
- Scene 2 is an original explanatory timeline. It intentionally does not reproduce a paper figure or
  claim that Sentinel-2 delivered the cited 18-day result. Keep the citation visible for the full hold.
- Scene 3 uses generic interface primitives rather than a fabricated product screenshot. Transition
  from its conversation card into the actual recorded 1415 Agri interface.
- Scene 5 depicts the product interaction conceptually. End the scene on the real **Estimate field
  boundary** and **Confirm boundary** controls as specified in the script.
- Scenes 6 and 15 reference the canonical transparent PNG at
  `core/static/core/brand/1415-agri-logo.png`; keep the repository layout intact when rendering.
- Scene 7 uses text labels, not invented partner logos. Reveal nodes from left to right, then reveal the
  private satellite path beneath them.

## Suggested scene timing

- **Scene 1 (7 s):** visible layer 0–2 s; wipe `#infrared-wipe` from right edge toward x=1105 over
  2–5 s; reveal the second copy line at 3 s.
- **Scene 2 (16 s):** day 46 card 0–5 s; `+18 DAYS` 5–8 s; day 64 card 8–13 s; hold citation 13–16 s.
- **Scene 3 (16 s):** reveal specialist cards every 0.45 s; converge at 4 s; show Gemini conversation
  from 5 s; hold the bottom claim for the final 4 s.
- **Scene 4 (8 s):** reveal one line every 1.4 s and hold the full statement for at least 3 s.
- **Scene 5 (12 s):** legacy path 0–3 s; reference point 3–7 s; confirmed boundary 7–11 s; transition
  into the real controls during the last second.
- **Scene 6 (6 s):** logo 0–2.5 s; positioning 2.5–4.5 s; proof transition 4.5–6 s.
- **Scene 7 (13 s):** reasoning path 0–7 s; data path 7–11 s; stack footer hold 11–13 s.
- **Scene 15 (8 s):** begin with the field motif; reveal the principle in three beats; resolve to logo,
  URL and hackathon badge for the final 3 s.

## Render guidance

Render at 1920×1080, 30 fps. Preserve aspect ratio; never stretch a card or the canonical logo.
Place burned-in subtitles above the SVG layer and keep them within the established two-line limit.
The title text sits clear of the usual bottom subtitle safe area. When animating in a browser or motion
tool, target the named groups with opacity/transform changes rather than splitting the master artwork.
