# Timed English script — 3:53 master

The narration below is final copy. Bracketed production notes are not spoken. Keep the words exactly
aligned with `narration_en.txt` and `captions_en.srt` unless a verified on-screen result requires a
factual correction.

## 00:00–00:18 — Problem and value

**Visual:** `assets/01_title_1920x1080.svg` for two seconds, then the English landing page. Move down
just enough to reveal the invisible-spectrum section. No cursor circles or rapid scrolling.

**Narration:**

> Farmers can walk a field, but they cannot see red-edge, near-infrared, or short-wave infrared.
> Sentinel-2 captures those signals, yet turning a season of satellite data into a useful field visit
> usually requires specialist software. 1415 Agri makes that workflow accessible.

**Judge signal:** clear operational problem, public satellite source, concrete value proposition.

## 00:18–00:34 — Agentic architecture

**Visual:** `assets/02_architecture_1920x1080.svg`. Reveal the green conversational path first, then
the blue deterministic analysis path. Keep all labels visible for at least three seconds.

**Narration:**

> This is a Collaborative Partner, not a diagnostic chatbot. Gemini 3.5 Flash leads the workflow
> through Google ADK and MCP tools. Deterministic services measure the pixels; the farmer confirms the
> boundary and decides what to inspect.

**Judge signal:** Gemini is essential to orchestration and explanation, while numerical work remains
reproducible.

## 00:34–00:55 — Authorized field context

**Visual:** begin the uninterrupted live-workflow master. Show the form already filled except for one
harmless final interaction. Use English UI, `Taquarituba Family Field`, Soybeans, 15 October 2025 to
15 March 2026, 36 hectares, and the authorized reference point. Keep Guided demonstration off. Apply
`assets/callout_01_real_data.svg`. Hide trailing coordinate decimals in the edit.

**Narration:**

> This family field in Taquarituba is authorized for this demonstration. We provide soybeans, the
> 2025–26 season, 36 hectares, and a reference point. Guided mode is off, so every result comes from
> the real satellite workflow now reaching Django on Cloud Run, with a traceable request ID for replay
> and auditing.

## 00:55–01:15 — Human confirmation

**Visual:** show the real boundary-ready UI without exposing coordinates, followed by
`assets/scene_04_boundary_control.svg`. The card records the actual 30.3-hectare, 74%-confidence
proposal and the explicit confirmation gate without reproducing the dense editable geometry.

**Narration:**

> The proposed polygon is never accepted silently. The service suggests a boundary around the
> reference point, but the farmer stays in control. I can inspect the outline, correct a point,
> compare its 30.3-hectare estimate with the expected area, and explicitly confirm the cultivated area
> before analysis can start.

## 01:15–01:55 — Real processing

**Visual:** show the captured checkpoints from the measured live run in chronological order. Guided
mode remains off. The spoken copy states the measured 93-second runtime, so the 40-second edit never
implies that the omitted wall-clock time was instantaneous.

**Narration:**

> Cloud Tasks now delivers an authenticated job to a private Cloud Run worker. It searches real
> Sentinel-2 Level-2A observations, reads bounded image windows, applies cloud masking, aligns the
> dates, and calculates NDVI, NDRE, and NDMI. In this live run, the worker selects five usable
> observations from October through March and completes the analysis in 93 seconds. Progress and
> provenance are persisted at every stage. The same analysis ID ties every stage together. These are
> checkpoints from the live processing run. Gemini does not calculate or invent these pixels; it
> reasons only after deterministic processing is complete.

**Elastic rule:** if the measured wait exceeds the available 40 seconds, do not speed it up. End the
first excerpt with the visible analysis ID, insert a plain `Processing continued — same analysis ID`
card with the truthful elapsed time, and resume on the same ID. Preserve the unedited source master
for audit. No statement may imply that the omitted wall-clock wait was shown live.

## 01:55–02:22 — Evidence and limits

**Visual:** pause on the real area map, scene dates, NDVI, NDRE, NDMI, cloud cover, mission/bands,
provenance, and GeoJSON control.

**Narration:**

> The output divides one field into four areas with different spectral trajectories. We compare five
> usable Sentinel-2 observations and inspect every value behind the map. Evidence stays connected to
> dates, NDVI, NDRE, NDMI, mission metadata, source bands, cloud coverage, and provenance. Area One
> differs most clearly. This is a scouting priority map, not a diagnosis.

## 02:22–02:53 — Grounded Gemini answer

**Visual:** in Ask Gemini, enter the exact grounded prompt from `shot_list.md`. Use voice only if the
ten-minute recognition test was flawless; otherwise type. Show the answer, then expand the technology
trace. Apply `assets/callout_04_gemini_adk_mcp.svg` while the trace is legible.

**Narration:**

> Gemini receives typed evidence, not raw rasters. Asked where to scout first, it selects Area One,
> compares February twenty-second and March fourteenth, cites NDVI, NDRE, and NDMI, and returns three
> field steps without diagnosis. The table is the field-wide baseline; the cited values are persisted
> Area One observations from get_analysis_evidence. The trace exposes Gemini 3.5 Flash, Google ADK,
> the temporal specialist, and MCP tools, so the chain remains auditable.

## 02:53–03:15 — Agent-triggered state change

**Visual:** enter the exact mutating prompt from `shot_list.md`. Show the returned analysis ID or
queued state and expand the trace until `request_field_analysis` is legible. Apply
`assets/callout_05_agent_action.svg`.

**Narration:**

> This second turn changes application state. With explicit permission, the temporal specialist calls
> request_field_analysis, creates a three-area comparison, and queues work. The returned analysis ID
> connects it to worker logs, while idempotency prevents a Gemini retry from creating duplicate jobs
> and preserves an audit trail.

Do not say or imply that the second analysis completed unless its persisted state visibly says so.

## 03:15–03:27 — Feedback under farmer control

**Visual:** submit one honest feedback rating. Keep the analysis/session association visible if the UI
shows it. Do not claim retraining.

**Narration:**

> The farmer can request a different grouping and rate the explanation. Feedback is stored with the
> exact analysis and conversation; it never silently changes the scientific result.

## 03:27–03:44 — Google Cloud proof

**Visual:** show a sanitized Cloud Console clip containing web, worker, MCP, and agent services, all
four service names and revisions legible, followed by one safe log entry from the recorded window.
Crop or mask the account and billing regions. Apply `assets/callout_06_cloud_roles.svg`.

**Narration:**

> One immutable container runs as four separately permissioned Cloud Run services. Vertex AI provides
> Gemini; Firestore stores evidence and feedback; Cloud Storage stores artifacts; and Cloud Tasks runs
> the satellite workflow. Private endpoints reject anonymous calls.

## 03:44–03:53 — Close

**Visual:** `assets/03_closing_1920x1080.svg`, then a half-second fade to off-white. Do not add credits
or an end screen after 03:53.

**Narration:**

> 1415 Agri turns invisible satellite signals into an understandable, accountable field visit for
> farmers left out of precision agriculture.

## Locked prompts

Grounded question:

> Using only the persisted Sentinel-2 evidence, which area should I scout first? Cite the dates and
> the NDVI, NDRE, and NDMI patterns you used, then give me a three-step field inspection plan. Do not
> diagnose a cause.

Agent action:

> Queue a new comparison with three areas for this confirmed field. Do not duplicate it if the request
> is retried.

If the completed result already contains three areas, replace only `three` with `four` in the action
prompt and leave the rest unchanged.
