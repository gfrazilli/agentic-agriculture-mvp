# Four-minute demo video script

Target runtime: **3:50–3:58**. The official maximum is four minutes, and only the first four minutes
may be evaluated. Publish the final video at `[PUBLIC_VIDEO_URL]` as a publicly visible YouTube or
Vimeo video. Use this English narration, or add accurate English subtitles to a Portuguese narration.

## Before recording

- Use only `[AUTHORIZED_FIELD_NAME]` and imagery that the team is authorized to demonstrate.
- Open `[FINAL_HOSTED_URL]` in a clean browser profile and confirm that the `.run.app` address is
  visible during the live sequence.
- Enter the authorized field details in advance, but do not submit the form yet.
- Turn **Guided demonstration** off. The judged proof segment must use the real pipeline.
- Run `python manage.py demo_preflight` with the deployed web, agent, and MCP endpoints.
- Verify one complete real run before recording. Keep it as a fallback result, but record a new live
  action for the video.
- Open a second tab on the Google Cloud Console with the four Cloud Run services visible and a third
  tab with safe service or Vertex AI logs. Hide billing data, email addresses, tokens, credentials,
  coordinates that should remain private, and all secret values.
- Set the browser and Cloud Console zoom so dates, scene count, agent/tool trace, and service names are
  legible at 1080p.
- Record system audio only if needed. Avoid background music that competes with the narration.

## Timeline and narration

### 0:00–0:20 — The problem

**Visual:** Presenter on camera, then a clean title card: “Agentic Agriculture — The Collaborative
Partner.”

**Narration:**

> A small farmer can walk a field, but cannot see red-edge, near-infrared, or short-wave infrared.
> Public satellite data contains those signals, yet turning it into a useful field visit usually
> requires specialist software. Agentic Agriculture makes that workflow accessible without
> pretending that a spectral difference is a diagnosis.

### 0:20–0:38 — The value and category

**Visual:** Show the architecture diagram from `ARCHITECTURE_NARRATIVE.md`. Highlight the farmer,
Gemini/ADK, deterministic worker, and Google Cloud boundary.

**Narration:**

> This is our Collaborative Partner. Gemini leads the conversation, asks for missing decisions, and
> uses tools. A deterministic pipeline measures the imagery. The farmer confirms the field and keeps
> control of what happens next.

### 0:38–2:25 — Uninterrupted proof of action

Keep this entire segment continuous and at normal speed. Do not replace the processing wait with a
mock animation or an earlier result.

#### 0:38–0:58 — Field context

**Visual:** Switch to `[FINAL_HOSTED_URL]`. Keep the `.run.app` address visible. Briefly show the
already-entered crop, season, area, and permitted reference point for `[AUTHORIZED_FIELD_NAME]`, then
click **Estimate field boundary**.

**Narration:**

> I am using a field that my family authorized for this demonstration. A reference point and the
> crop season are enough to start. This request is now reaching our Django service on Cloud Run.

#### 0:58–1:18 — Human confirmation

**Visual:** Show the proposed polygon and confidence metadata. Move one vertex or edit one coordinate,
update the map, and click **This boundary is correct**.

**Narration:**

> The proposal is not a legal boundary and it is never accepted silently. I can correct the geometry,
> and processing remains blocked until I explicitly confirm it.

#### 1:18–1:58 — Real background processing

**Visual:** Keep the progress screen visible. If the run takes longer, shorten the introduction in the
final edit, not this uninterrupted sequence. When possible, briefly place the Cloud Run worker log
beside the browser without cutting away.

**Narration:**

> Cloud Tasks has delivered an authenticated job to a private Cloud Run worker. It searches real
> Sentinel-2 L2A observations, reads bounded image windows, applies calibration and cloud masking,
> aligns the dates, calculates NDVI, NDRE, and NDMI, and groups relative trajectories. Gemini does not
> calculate or invent these pixels.

Do not state a fixed runtime, scene count, or zone count in advance. Narrate only the values that are
visible in this recorded run. A prior local validation took about 42 seconds and produced three scenes
and two zones, but those values are not a substitute for the run on screen.

#### 1:58–2:25 — Evidence becomes visible

**Visual:** When the result arrives, point to the zone map, relative labels, scene dates, index values,
cloud cover, source/provenance, and GeoJSON download. Keep at least one exact scene identifier or
provenance detail legible.

**Narration:**

> The output is intentionally simple: these parts of one field followed different spectral
> trajectories across these exact dates. The map tells the farmer where to inspect. It does not claim
> to know whether the cause is water, soil, pests, disease, or management.

### 2:25–3:08 — Gemini uses evidence and takes action

#### 2:25–2:48 — Grounded explanation

**Visual:** In **Ask Gemini**, enter: “Which zone should I inspect first, and what evidence supports
that choice?” Send the request. Show the final answer and expand **Technology used for this response**
so the agent and tools are legible.

**Narration:**

> The browser calls only our protected Django gateway. Behind it, a private Google ADK service uses
> Gemini 3.5 and delegates to a specialist. The answer is grounded in persisted scene and zone
> evidence, and the trace shows which agent and tools were used.

#### 2:48–3:08 — Mutating action

**Visual:** Ask: “Please request a new three-zone analysis for this confirmed field.” Show the returned
analysis identifier or queued status and the `request_field_analysis` tool in the trace. If the
recorded run already used three zones, request a different number from two to seven.

**Narration:**

> This is more than retrieval. With explicit permission, the temporal specialist can create and queue
> another analysis. The action is idempotent, so a model retry returns the same request instead of
> duplicating work.

Do not claim that this second request completed unless its persisted state visibly says `completed`.

### 3:08–3:30 — Adaptation and feedback

**Visual:** Show the zone-count control and then submit one honest feedback rating. Do not claim that
the rating retrains the model.

**Narration:**

> The farmer can ask for another grouping, correct the field context, and rate the explanation. That
> feedback is stored with the exact analysis and conversation, creating a safe foundation for future
> adaptation without silently changing the scientific result.

### 3:30–3:47 — Undeniable Google Cloud proof

**Visual:** Show the Google Cloud Console. The project and region may be visible. Show the web, worker,
MCP, and agent Cloud Run services, then a safe Vertex AI or service-log entry from the demonstration
window. Do not display secrets or credentials.

**Narration:**

> The same container revision runs as four separately permissioned Cloud Run services. Vertex AI
> provides Gemini, Firestore stores evidence and feedback, Cloud Storage stores artifacts, and Cloud
> Tasks runs the satellite workflow. The worker, MCP, and ADK endpoints reject unauthenticated calls.

### 3:47–3:58 — Close

**Visual:** Return to the final zone map and project name.

**Narration:**

> Agentic Agriculture lets Gemini make advanced sensing understandable, actionable, and accountable
> for a farmer who was previously left out of precision agriculture.

End before 4:00. Do not add credits after the limit.

## Required visible proof checklist

- The live `.run.app` address.
- The authorized field context and an explicit boundary confirmation.
- One uninterrupted real action with progress and a resulting UI or persisted-state change.
- Real scene dates or identifiers, indices, zones, and provenance.
- A Gemini answer plus ADK agent/tool trace.
- The `request_field_analysis` action or another visible agent-triggered mutation.
- The four Cloud Run roles and safe Google Cloud/Vertex AI execution evidence.
- The non-diagnostic scope statement.

## Editing rules for this video

- It is acceptable to edit the presenter introduction and architecture explanation for pace.
- Keep the proof-of-action sequence continuous and at normal speed.
- Never splice a fixture result into a sequence presented as real processing.
- If a network failure occurs, record the entire live segment again.
- Use captions large enough to read on a laptop screen.
- Show third-party marks only when necessary to identify the technology or data source; do not imply
  sponsorship beyond Google organizing the contest.
- Upload early enough for YouTube or Vimeo to finish public processing before the deadline.

## Official video requirements

The [official rules](https://allthingsagentichackathon.devpost.com/rules) require a problem overview,
value proposition, app demonstration, and visible proof that the backend runs on Google Cloud. The
video must be publicly visible on YouTube or Vimeo, must be in English or include English subtitles,
and must not exceed four minutes if every part is to be evaluated. The
[official FAQ](https://allthingsagentichackathon.devpost.com/details/faqs) reiterates that only the
first four minutes are evaluated and that Cloud proof is mandatory.
