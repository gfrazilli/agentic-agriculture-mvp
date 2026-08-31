# Judge guide

## The 60-second version

**1415 Agri** is a Collaborative Partner for farmers who cannot afford a conventional
precision-agriculture stack. A farmer identifies a field, reviews the proposed cultivated boundary,
and keeps control by explicitly confirming or correcting it. The system then converts a season of
real Sentinel-2 multispectral observations into a small set of relative-development zones. A Gemini
3.5+ application built with Google ADK can request the analysis, consult real catalog metadata through
a private MCP service, retrieve persisted evidence, and explain what deserves field inspection.

The twist is the division of responsibility. Gemini leads the interaction and decides which narrow
tool or specialist to use, while deterministic code performs every pixel calculation. The model never
invents reflectance values, redraws a field, or diagnoses a pest, disease, soil, water, yield, or
treatment problem. It turns evidence that is difficult for a small farmer to access into a guided,
inspectable workflow.

Category: **The Collaborative Partner**.

## Official judging mechanics

- **Stage One is pass/fail.** The entry must include every required submission component, address one
  challenge, and reasonably apply the mandatory technology requirements.
- **Stage Two uses three weighted criteria.** Judges assign a score from 1 to 5 for each criterion:
  Innovation and Operational Utility (40%), Architectural Discipline and Tech Stack (30%), and Demo
  and Production Readiness (30%).
- **Stage Three adds only the bonuses listed in the rules.** Qualifying public build content adds up to
  0.2, a qualifying social post adds up to 0.2, and each successfully integrated additional Google AI
  model adds 0.2 up to 0.6 for models.
- The published maximum final score is 6. Ties are compared in the listed criterion order before a
  judge vote.

For Collaborative Partner, Innovation asks whether the agent actively synthesizes or mutates data and
whether it ingests an unusual, messy, or complex data stream. The architecture subcriterion asks
about data schema, embedding choices, and large-context efficiency. Demo scoring emphasizes clear
documentation, visible Google Cloud deployment, and an unedited live proof of action through logs,
database changes, or UI changes.

## Fastest evaluation path

1. Open https://1415agri.com/ and use the credentials supplied in the Devpost testing instructions.
2. Switch the interface to English if needed.
3. Register the authorized demonstration field, `[AUTHORIZED_FIELD_NAME]`, using its season and
   reference location.
4. Review and confirm the proposed polygon. The analysis cannot start before confirmation.
5. Start a real analysis and watch the progress state move through scene acquisition, spectral-index
   calculation, and zone grouping.
6. Inspect the scene dates, NDVI/NDRE/NDMI values, relative zones, provenance, and downloadable
   GeoJSON.
7. Ask Gemini which zone deserves inspection first. Expand the execution trace to see the ADK agent
   and tools used for the answer.
8. Ask Gemini to request a new two-zone analysis. The action tool is idempotent, returns an analysis
   identifier, and enqueues background work only after checking the confirmed field state.
9. Record whether the explanation was helpful, unclear, or not helpful. Feedback is attached to the
   exact analysis and agent session.

The public repository is
[gfrazilli/agentic-agriculture-mvp](https://github.com/gfrazilli/agentic-agriculture-mvp).

## Why Collaborative Partner is the correct track

The official challenge asks this track to lead, ask clarifying questions, guide the user step by step,
capture feedback, and adapt to how the user thinks. This project implements those behaviors through:

- a four-step field, boundary, analysis, and evidence journey;
- a coordinator that asks at most one clarification at a time and delegates to narrow specialists;
- mandatory farmer correction or confirmation before the proposed boundary becomes authoritative;
- user-selected re-grouping from two to seven zones without pretending that one grouping is the only
  truth;
- voice or text agent sessions grounded in the selected field and analysis;
- persisted feedback tied to the exact answer context.

The rules use a different label, **The Evolving Knowledge Engine**, inside the architecture criterion.
An official Devpost manager clarified that this label maps to Collaborative Partner. Therefore, the
relevant architectural emphasis is the data architecture and efficient management of large context,
not the enterprise-fleet subcriterion.

## Evidence against the official scorecard

### Innovation and operational utility — 40%

**Real friction.** Multispectral satellite archives, cloud masks, calibration metadata, reprojection,
time-series comparison, and geospatial outputs are far beyond a simple chat query. The product makes
that workflow approachable to a farmer through one guided interaction.

**Autonomous action.** The ADK temporal specialist can call `request_field_analysis` after an explicit
request. That action validates the field, creates or safely replays an analysis request, and schedules
the private worker. The agent is not limited to describing what another system could do.

**Data synthesis and mutation.** The worker transforms real STAC metadata and bounded Sentinel-2 COG
windows into calibrated bands, cloud-masked NDVI/NDRE/NDMI time series, relative-development zones,
GeoJSON, preview artifacts, and persisted evidence. Re-grouping mutates the representation while
retaining provenance to the source analysis.

**Unusual data stream.** The input combines scene metadata, multiple visible and infrared raster bands,
a scene-classification mask, field geometry, crop-season dates, and user corrections. Raw pixel arrays
stay out of the model context; Gemini receives compact, typed evidence.

**The twist.** Sentinel-2 measures red-edge, near-infrared, and short-wave infrared signals outside
normal human vision. Deterministic processing measures the differences; Gemini makes them
understandable and turns them into a next inspection step.

Repository evidence:

- `agentic_agriculture/agent.py` — coordinator and three ADK specialists;
- `agentic_agriculture/tools.py` — grounded read tools and the idempotent analysis action;
- `geospatial/pipeline.py` — real multispectral acquisition and transformation;
- `geospatial/zoning.py` — deterministic spectral indices and zone grouping;
- `agriculture/services/application.py` — confirmation, quota, idempotency, re-grouping, and feedback;
- `core/static/core/farmer-app.js` — guided workflow, agent trace, voice/text entry, and feedback.

### Architectural discipline and tech stack — 30%

**Separation of concerns.** The public Django service, private ADK service, private MCP service, and
private Sentinel worker run as separate Cloud Run roles. Firestore holds structured state, Cloud
Storage holds artifacts, and Cloud Tasks delivers long-running analysis work.

**Model boundary.** Gemini 3.5+ performs intent understanding, delegation, tool selection, explanation,
and safe next-step guidance. It does not calculate indices or assign pixels to zones. This makes every
numeric claim traceable to deterministic evidence.

**Context efficiency.** The MCP tools expose compact scene metadata and observation plans. Repository
tools project strict evidence envelopes. Large raster arrays never enter Gemini's context window.

**State and reliability.** Mutations require idempotency keys; the agent action derives one from the
confirmed field state and requested zone count. Cloud Tasks retries are paired with processing leases.
The worker resumes after a stale lease and refuses active duplicates. Contracts validate all persisted
records.

**Security.** The browser calls only Django. Django, the agent, MCP, worker, and task invoker have
separate service identities. Private Cloud Run calls use short-lived Google identity tokens. Secrets
live in Secret Manager, remote imagery hosts are allowlisted, and COG reads are bounded.

Repository evidence:

- `agriculture/adapters/agent_api.py` — authenticated web-to-ADK gateway and compact execution trace;
- `geospatial/mcp_server.py` — stateless, read-only MCP surface;
- `agriculture/adapters/tasks.py` and `agriculture/internal/views.py` — authenticated task delivery;
- `agriculture/schemas/contracts.py` — typed state and provenance contracts;
- `infra/gcp/bootstrap.sh` and `infra/gcp/deploy.sh` — reproducible IAM and deployment topology;
- `docs/submission/ARCHITECTURE_NARRATIVE.md` — architecture diagram and end-to-end data flow.

### Demo and production readiness — 30%

The video must contain an uninterrupted proof-of-action sequence. It should visibly show a real user
request, the analysis progressing, a state or UI change, the evidence result, the Gemini tool trace,
and Google Cloud proof. The strongest evidence is the live `.run.app` address followed by the Cloud
Run services and Vertex AI or service logs for the same demonstration window.

Reproducibility is supported by Docker, checked-in Cloud provisioning/deployment/smoke scripts, local
and Cloud Run instructions, a read-only `demo_preflight` command, typed API contracts, and automated
tests. The video plan is in `docs/submission/VIDEO_SCRIPT.md`.

## Validation status and claim discipline

A locally executed real-data pipeline validation completed in approximately **42 seconds** and
produced **three real scenes and two relative-development zones**. Local integration checks also
showed the ADK, Gemini, and MCP path functioning. These are **local validation observations**, not yet
submission proof. They must remain described that way until a final run manifest, persisted result,
and matching screen recording are captured.

Do not claim any of the following:

- that a zone is an anomaly in the diagnostic sense;
- that the system identifies the cause of a difference;
- that a satellite result proves water stress, pests, disease, soil condition, yield, or a treatment;
- that the sample result came from `[AUTHORIZED_FIELD_NAME]` unless its scene identifiers, geometry,
  dates, and run record verify that fact;
- that persisted feedback automatically retrains Gemini or changes future model weights;
- that the local 42-second observation is a guaranteed production runtime.

The defensible result is narrower and useful: the product finds areas within one field that developed
differently across the selected observations and helps the farmer decide where to inspect.

## Official sources

- [Official rules](https://allthingsagentichackathon.devpost.com/rules) — eligibility, submission
  requirements, technology requirements, video rules, judging, and prizes.
- [Official challenge overview](https://allthingsagentichackathon.devpost.com/) — category definitions
  and the submission checklist.
- [Official FAQ](https://allthingsagentichackathon.devpost.com/details/faqs) — one-category rule,
  four-minute cutoff, public YouTube/Vimeo requirement, English/subtitle rule, Cloud proof, and the
  post-deadline freeze.
- [Official Devpost manager clarification](https://allthingsagentichackathon.devpost.com/forum_topics/44900-rules-track-names-multi-agent-nexus-etc-vs-official-categories-which-applies-to-fleet)
  — maps The Evolving Knowledge Engine to Collaborative Partner and confirms that the official rules
  control bonus scoring.
