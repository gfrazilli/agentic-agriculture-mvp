# Operational shot list

## Capture setup

- Create a clean 1920×1080 capture window at 100% browser zoom; use F11 where practical.
- Disable notifications and close or hide unrelated tabs. Remove password-manager popups.
- Keep the cursor at normal size and move it deliberately; pause 0.8–1.2 seconds before every click.
- Record no microphone audio. Narration is added later from scene-separated files.
- Do not show the family reference screenshot, exact trailing coordinate decimals, browser autofill,
  email addresses, billing panels, tokens, cookies, request headers, or secret values.
- Guided demonstration must be off for every live analysis capture.
- Save masters outside Git under `C:\Users\giova\Videos\1415-Agri-Hackathon\raw\`.

## Required capture files

### `01_landing_master.mp4`

- **Target usable duration:** 16 seconds.
- Start at the top of the English landing page with no modal open.
- Hold the hero for five seconds, then make one slow scroll to the invisible-spectrum section.
- Keep the canonical logo fully visible and undistorted.
- Pass if the value proposition and one spectrum explanation are readable at 1080p.

### `02_live_workflow_master.mp4`

- **Record continuously:** filled form through the first completed result; never stop during
  boundary confirmation or processing.
- Start with:
  - Field name: `Taquarituba Family Field`
  - Crop: `Soybeans`
  - Planting date: `2025-10-15`
  - Monitoring end: `2026-03-15`
  - Area: `36`
  - Unit: `Hectares`
  - Latitude: authorized exact value supplied separately
  - Longitude: authorized exact value supplied separately
  - Guided demonstration: unchecked
- Make one visible, harmless field interaction, then click **Estimate field boundary**.
- On the map, pause, move one vertex by a small amount that still follows the authorized cultivated
  area, pause again, and explicitly confirm.
- Leave the progress UI visible until completion; do not reload or switch to a prior result.
- At completion, show in this order: map, area summary, scene dates, NDVI/NDRE/NDMI evidence,
  provenance, and GeoJSON control.
- Pass only if the same run produces a visible completed analysis with real scenes and evidence.
- Retain this complete master even if the final edit transparently condenses a long wait.

### `03_gemini_grounded_master.mp4`

- Start on the completed analysis from the previous capture.
- Enter exactly:

  `Using only the persisted Sentinel-2 evidence, which area should I scout first? Cite the dates and the NDVI, NDRE, and NDMI patterns you used, then give me a three-step field inspection plan. Do not diagnose a cause.`

- Voice input may be used only if a separate test reproduced the complete sentence perfectly within a
  ten-minute limit. Otherwise type it.
- Hold on the final answer long enough to read its cited evidence and three-step plan.
- Expand the technology trace and hold until model, framework, specialist, and tool names are legible.
- Pass only if the answer identifies one priority area, cites real stored observations, offers three
  inspection steps, and avoids a causal diagnosis.
- If the answer fails, rephrase/retry the conversation only; do not launch another satellite analysis.

### `04_agent_action_master.mp4`

- Inspect the completed result's current number of areas before recording.
- If it is not three, enter:

  `Queue a new comparison with three areas for this confirmed field. Do not duplicate it if the request is retried.`

- If it is three, substitute `four areas` and change nothing else.
- Hold the returned queued/replayed status and analysis ID, then expand the trace until
  `request_field_analysis` is legible.
- Pass only if an application-state change or safe replay is visible. Never claim completion from a
  merely queued status.

### `05_feedback_master.mp4`

- Start on the same analysis/conversation.
- Submit one honest rating and hold the confirmation for three seconds.
- Pass if the UI visibly accepts the feedback; do not mention training or model updates.

### `06_cloud_proof_master.mp4`

- Use a sanitized Google Cloud Console window.
- Show the web, worker, MCP, and agent Cloud Run services together, with revisions/status legible.
- Show one safe log line whose timestamp matches the demonstration window.
- Optionally reveal the public `.run.app` origin, but never reveal headers, credentials, secrets,
  billing, or the signed-in account identity.
- Pass if the four roles and one execution artifact are independently readable at 1080p.

## Pickup clips

- `P01_result_detail.mp4`: slow scroll across dates, indices, and provenance if the continuous master
  moved too quickly after completion.
- `P02_trace_detail.mp4`: static three-to-five-second trace hold if tool names are too small.
- `P03_cloud_log_detail.mp4`: sanitized log hold if console navigation created visual noise.

Pickups may clarify existing proof but must not be spliced into the live sequence in a way that
misrepresents them as the same uninterrupted action.

## Final capture manifest

For each accepted file, record capture date/time, browser language, analysis ID where applicable,
duration, resolution, and privacy reviewer initials in a separate local manifest. Do not place exact
coordinates, credentials, or tokens in that manifest.

