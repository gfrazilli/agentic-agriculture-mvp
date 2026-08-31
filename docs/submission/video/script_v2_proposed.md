# 1415 Agri — proposed audiovisual script V2

**Status:** implemented V2 package, awaiting user approval before publication.

**Target duration:** 3:56

**Language:** English narration and English captions

**Narration pace:** deliberate in the opening; measured per scene in the technical sequence

**Creative direction:** keynote-like, minimal, one claim per screen, no generic “AI glow” imagery

## Production logic

- **00:00–00:59:** visual, advertising-led opening. Real field/satellite material, an original research
  timeline, restrained typography and the green 1415 Agri statement.
- **00:59–01:18:** product reveal and a simple animated architecture path.
- **01:18–02:25:** real application captures from the measured field workflow, boundary confirmation,
  processing checkpoints, result and persisted Sentinel-2 evidence.
- **02:25–03:21:** the natural Gemini question, grounded answer path, real state-changing action trace,
  and persisted feedback.
- **03:21–03:48:** sanitized Google Cloud Console proof and matching execution log.
- **03:48–03:56:** concise brand close.

The spoken script does not name the Collaborative Partner category and does not repeat defensive
limitations. A small `CATEGORY · COLLABORATIVE PARTNER` badge may appear once on the architecture
scene. The behavior proves the category.

---

## 00:00–00:07 — Scene 1 · The human limit

### Purpose

Create an immediate, universally understandable tension.

### Visual

- Full-screen real aerial image of cultivated land.
- Begin in natural color and slowly introduce a false-color infrared layer.
- No logo, interface, diagram or cursor yet.
- Display one sentence at a time with generous empty space.

### On-screen text — not additional narration

`FARMERS CAN WALK A FIELD.`

`BUT THEY CANNOT SEE INFRARED.`

### Spoken

> Farmers can walk a field. But they cannot see infrared.

### Do not say or show

- Do not list NDVI, NDRE or NDMI yet.
- Do not show generic stock technology visuals.

### Judge signal

The problem is understandable before the product is introduced.

---

## 00:07–00:23 — Scene 2 · A signal before the symptom

### Purpose

Show, with scientific support, why invisible spectra matter.

### Visual

- Original minimalist timeline; do not reproduce a figure from the paper.
- Left: stylized soybean canopy and red-edge curve at 46 days after planting.
- Center: a clean `+18 DAYS` transition.
- Right: visible wilting at 64 days after planting.
- End by zooming from the experimental graphic into a Sentinel-2 view of a complete field.

### On-screen text — not additional narration

`DAY 46 · RED-EDGE DIFFERENCE · NO VISIBLE WILTING`

`+18 DAYS`

`DAY 64 · VISIBLE WILTING`

Small citation footer:

`Jones et al. · The Plant Phenome Journal 7 (2024), e70009`

`Multispectral UAV soybean study · DOI 10.1002/ppj2.70009`

### Spoken

> Multispectral sensors can. In a peer-reviewed soybean drought study, drone-based red-edge imagery
> revealed differences between plots at day forty-six. Wilting became visible at day sixty-four—
> eighteen days later. Sentinel-2 carries multispectral bands across fields and
> seasons.

### Do not say or show

- Do not say that Sentinel-2 detected wilting 18 days early.
- Do not call red-edge a diagnosis.
- Do not imply that 18 days is a guaranteed lead time.

### Judge signal

A memorable, source-backed reason to use data outside human vision.

---

## 00:23–00:39 — Scene 3 · The real barrier

### Purpose

Define the operational friction and make Gemini essential to the solution.

### Visual

- Fast, clean sequence of five steps:
  1. search satellite scenes;
  2. reject clouds;
  3. align dates;
  4. process spectral bands;
  5. interpret a season.
- Each specialist screen collapses into one simple Gemini conversation.
- Use real interface fragments or original diagrams, not stock dashboards.

### On-screen text — not additional narration

`THE DATA EXISTS.`

`THE WORKFLOW IS THE BARRIER.`

### Spoken

> These analyses exist. But finding clear images, aligning dates, processing spectral bands, and
> interpreting results takes specialist software and hours. Gemini 3.5 can coordinate the workflow
> and explain it in language a farmer can use.

### Do not say or show

- Do not describe Gemini as merely a chatbot.
- Do not name every cloud service in the opening.

### Judge signal

Innovation and operational utility: Gemini removes a real workflow barrier rather than adding a chat
box to an existing product.

---

## 00:39–00:47 — Scene 4 · Product principle

### Purpose

Land the central idea in one memorable sentence.

### Visual

- Use the existing green section from the English landing page full-screen.
- Reveal one line at a time, synchronized with the voice.
- No competing callouts.

### On-screen text and spoken

> Satellites measure. Gemini reasons. The farmer decides.

### Judge signal

Clear division of responsibility, expressed positively and memorably.

---

## 00:47–00:59 — Scene 5 · Two-click field mapping

### Purpose

Introduce the second concrete friction before the technical demo.

### Visual

- Minimal three-part animation:
  - a person walking GPS points around a field;
  - a reference pin placed on a map;
  - a proposed polygon followed by a confirmation check.
- End on the real application's **Estimate field boundary** and **Confirm boundary** controls.

### On-screen text — not additional narration

`1 REFERENCE POINT`

`1 PROPOSED BOUNDARY`

`FARMER CONFIRMED`

### Spoken

> Field mapping means collecting GPS points and cleaning polygons. With 1415 Agri, one point becomes
> a proposed boundary for the farmer to review and confirm.
> Two clicks.

### Do not say or show

- Do not claim that Gemini calculates the polygon; the current geospatial service proposes it.
- Do not show precise coordinates.

### Judge signal

Another measurable reduction in real-world friction, immediately proved in the following demo.

---

## 00:59–01:05 — Scene 6 · Product reveal

### Purpose

Name the product and clearly transition from proposition to proof.

### Visual

- Clean canonical 1415 Agri logo on off-white.
- Subtle dissolve into the production architecture.

### On-screen text — not additional narration

`SATELLITE INTELLIGENCE FOR EVERY FIELD`

### Spoken

> Meet 1415 Agri.

---

## 01:05–01:18 — Scene 7 · The agentic path

### Purpose

Show that the product meets the required stack before the live execution begins.

### Visual

- Animate one path from left to right, never reveal the whole diagram at once:
  `Farmer → Gemini 3.5 Flash / Vertex AI → Google ADK specialists → MCP tools → deterministic services`.
- Show the second path beneath it:
  `Cloud Tasks → private worker → Sentinel-2 → Firestore / Cloud Storage`.
- Small badge only: `CATEGORY · COLLABORATIVE PARTNER`.

### On-screen text — not additional narration

`GEMINI 3.5 FLASH` · `VERTEX AI` · `GOOGLE ADK` · `MCP` · `GOOGLE CLOUD`

### Spoken

> Gemini 3.5 Flash runs on Vertex AI. Google ADK routes requests. MCP exposes typed evidence and action
> tools. Deterministic services handle geometry and pixel math.

### Judge signal

Mandatory stack plus disciplined separation between reasoning, tools and deterministic measurement.

---

## 01:18–01:40 — Scene 8 · Real field and confirmed boundary

### Purpose

Move from the keynote opening into documented proof from the authorized family field.

### Visual

- Use the real English application captures at 1920×1080, with Guided Demo unchecked.
- Show:
  - `Taquarituba Family Field`;
  - Soybeans;
  - 15 October 2025 to 15 March 2026;
  - estimated 36 hectares;
  - partially hidden reference coordinates.
- Show the saved form state followed by the real proposed boundary and its explicit confirmation.
- Use only gentle pan/zoom on the captured interface; do not label the montage as a continuous take.

### On-screen text — not additional narration

`AUTHORIZED FAMILY FIELD`

`GUIDED DEMO · OFF`

`REAL SENTINEL-2 WORKFLOW`

### Spoken

> Now, a real family field in Taquarituba, Brazil: soybeans, the 2025–26 season, about thirty-six
> hectares, and one reference point. Guided Demo is off. The system proposes the cultivated boundary;
> the farmer reviews it, adjusts it if needed, and confirms.

### Judge signal

Real inputs, explicit human confirmation and no fixture-driven demonstration.

---

## 01:40–02:05 — Scene 9 · Asynchronous satellite workflow

### Purpose

Make the invisible technical work visible without turning the video into a lecture.

### Visual

- Show chronological checkpoints from the measured production run: queued, running and completed.
- Keep the processing UI recognizable and expose the five selected observation dates as they appear.
- Use a uniform editorial rhythm without claiming real-time or uninterrupted capture.

### On-screen text — not additional narration

`REAL SENTINEL-2 L2A` · `5 USABLE OBSERVATIONS` · `93-SECOND RUN`

### Spoken

> The request goes through Cloud Tasks to a private Cloud Run worker. It searches real Sentinel-2
> Level-2A imagery, masks clouds, aligns dates, and calculates NDVI, NDRE, and NDMI. This run finds five
> usable observations from October through March and finishes in ninety-three seconds. The same
> analysis ID connects every stage, artifact, and log.

### Judge signal

Background execution, heavy data workflow, persistent state and failure-tolerant architecture.

---

## 02:05–02:25 — Scene 10 · A season becomes a priority

### Purpose

Convert technical processing into an operational decision.

### Visual

- Continue from the completion checkpoint into the captured production result.
- Hold the four-area map, then scroll deliberately through:
  - five dates;
  - NDVI, NDRE and NDMI;
  - cloud coverage;
  - Sentinel-2 mission and source bands;
  - processing provenance.
- End with Area 1 visually selected.

### Spoken

> One season becomes four areas with different spectral histories. Every highlight connects back to
> dates, index values, source bands, cloud coverage, and Sentinel-2 provenance. Area One shows the
> clearest change. That is where the next field visit begins.

### Judge signal

Complex multispectral and temporal data is synthesized into a concrete next action.

---

## 02:25–02:55 — Scene 11 · A farmer's question

### Purpose

Show useful Gemini reasoning with the simplest possible user interaction.

### Visual

- Present the exact natural question as a dedicated card, followed by the captured production answer:

  `Why should I check Area 1 first, and what should I look for when I get there?`

- Hold the real response long enough to identify:
  - February 22 and March 14;
  - NDVI, NDRE and NDMI changes;
  - three practical field checks.
- Open the technical trace and make these items legible:
  - Gemini 3.5 Flash;
  - Google ADK;
  - temporal specialist;
  - MCP/evidence tools.
- A small highlight may frame the evidence, but never change its values.

### On-screen text — not additional narration

`GROUNDED IN THIS FIELD'S STORED EVIDENCE`

### Spoken

> So the farmer asks a normal question: “Why should I check Area One first, and what should I look for
> when I get there?” Gemini calls the evidence tools and compares this field's stored history. It cites
> February twenty-second and March fourteenth, explains the changes in NDVI, NDRE, and NDMI, and gives
> three practical checks. The trace shows Gemini 3.5 Flash, Google ADK, the temporal specialist, and
> every MCP tool behind the answer.

### Acceptance condition for the shown response

The real answer must identify Area 1, use the actual persisted dates and indices, and offer three
useful checks. If it does not, retry only the conversation; do not create a replacement satellite
analysis.

### Judge signal

Gemini synthesizes unusual, typed field evidence instead of performing a generic retrieval query.

---

## 02:55–03:14 — Scene 12 · Gemini changes the system

### Purpose

Provide the clearest possible proof that this is an agent, not only an answer generator.

### Visual

- Show the real application state and the exact state-changing request:

  `Compare this field again using three areas.`

- Show the resulting queued analysis and new `analysis_id`.
- Expand the trace until `request_field_analysis` is legible.
- Do not describe the editorial sequence as an uninterrupted screen recording.

### Spoken

> Then Gemini acts. The farmer asks, “Compare this field again using three areas.” The trace shows the
> temporal specialist calling request_field_analysis, creating the new request, and queuing the
> background job. Idempotency makes retries safe instead of duplicating work.

### Judge signal

High-value state mutation, asynchronous execution and safe retry behavior beyond a standard chat loop.

---

## 03:14–03:21 — Scene 13 · Feedback stays with the work

### Purpose

Prove the Collaborative Partner feedback loop without saying the category aloud.

### Visual

- Submit one real, honest rating.
- Hold the confirmation that links feedback to the current analysis/conversation.

### Spoken

> The farmer rates the answer. That feedback stays linked to the exact analysis and conversation.

### Judge signal

Persistent context and feedback associated with the evidence that produced the answer.

---

## 03:21–03:48 — Scene 14 · Production proof on Google Cloud

### Purpose

Close the technical argument with independently verifiable deployment evidence.

### Visual

- Sanitized current Google Cloud Console capture.
- Show all four Cloud Run services together:
  - `agentic-agriculture-web`;
  - `agentic-agriculture-worker`;
  - `agentic-agriculture-mcp`;
  - `agentic-agriculture-agent`.
- Make the verified immutable image digest visible on the editorial proof card.
- Show the separately verified, safe worker and persisted-result log records without implying that
  the worker record itself contains the analysis ID.
- Crop account identity, billing, secrets, tokens and exact coordinates.

### On-screen text — not additional narration

`4 CLOUD RUN ROLES` · `1 IMMUTABLE CONTAINER` · `LEAST-PRIVILEGE IDENTITIES`

### Spoken

> Here is the production system running on Google Cloud: four permissioned Cloud Run services for web,
> worker, MCP, and agent, all from one immutable container. Vertex AI provides Gemini. Firestore stores
> state and evidence. Cloud Storage stores artifacts. Cloud Tasks runs the satellite workflow. The
> Cloud Logging records the worker request and the persisted analysis read.

### Judge signal

Google Cloud execution, modular architecture, scoped services, persistent state and proof tied to the
same demonstration.

---

## 03:48–03:56 — Scene 15 · Close

### Purpose

Return to the human benefit and leave the brand in memory.

### Visual

- Return briefly to the infrared field image from Scene 1.
- Dissolve into the green landing-page statement and canonical logo.
- End with `1415agri.com` and a small badge:
  `BUILT FOR THE ALL THINGS AGENTIC HACKATHON`.

### On-screen text — not additional narration

`SATELLITES MEASURE.`

`GEMINI REASONS.`

`THE FARMER DECIDES.`

### Spoken

> The signal was always there. Now small farmers can use it. 1415 Agri.

---

## Locked prompts for the future recording

### Useful farmer question

> Why should I check Area 1 first, and what should I look for when I get there?

### State-changing agent request

> Compare this field again using three areas.

## Why this version fits the judging brief

- **Innovation & Operational Utility — 40%:** shows a real access barrier, multi-temporal
  multispectral data, a practical scouting decision, and an agent-triggered state change.
- **Architectural Discipline & Tech Stack — 30%:** proves Gemini 3.5 Flash, Vertex AI, Google ADK,
  MCP, deterministic measurement, persistent state, Cloud Tasks, idempotency and permissioned Cloud
  Run roles.
- **Demo & Production Readiness — 30%:** combines real production captures, persisted Sentinel-2
  evidence, the state-changing MCP tool trace, replaceable scene-level editing, and visible Google
  Cloud deployment and logging evidence.

## Source and rule notes

- Jones et al. (2024), *Multi-sensor and multi-temporal high-throughput phenotyping for monitoring
  and early detection of water-limiting stress in soybean*, The Plant Phenome Journal 7, e70009.
  DOI: <https://doi.org/10.1002/ppj2.70009>.
- Official rules and judging criteria:
  <https://allthingsagentichackathon.devpost.com/rules>.
