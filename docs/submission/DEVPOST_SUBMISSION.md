# Devpost submission copy

## Project name

Agentic Agriculture

## Tagline

A Gemini partner that turns multispectral crop history into a farmer-guided map of where to inspect.

## Category

The Collaborative Partner

## Links

- Hosted project: `[FINAL_HOSTED_URL]`
- Code: https://github.com/gfrazilli/agentic-agriculture-mvp
- Demo video: `[PUBLIC_VIDEO_URL]`

## Short description

Agentic Agriculture gives small farmers a guided precision-agriculture workflow without requiring
them to operate satellite software. The farmer identifies a field and confirms its cultivated
boundary. A deterministic worker processes real Sentinel-2 red, red-edge, near-infrared, and
short-wave infrared observations into NDVI, NDRE, NDMI, and relative-development zones. Gemini 3.5+
and Google ADK lead the conversation, choose grounded tools, can request an analysis, and explain
which differences deserve a field visit without pretending to diagnose their cause.

## Inspiration

Precision-agriculture tools can be difficult to access for a small producer. Even when public
satellite imagery exists, using it requires knowledge of catalogs, spectral bands, cloud masks,
calibration, reprojection, time series, and geospatial files. The practical question is much simpler:
**which parts of my own field developed differently, so I know where to look?**

We built the project around that question. It deliberately stops before agronomic diagnosis. A
different spectral trajectory may have many causes, and a remote model should not turn uncertainty
into a prescription. The product instead reduces a large field to a small, evidence-backed inspection
map and gives the farmer control at every consequential step.

## What it does

1. The farmer enters crop-season dates, estimated area, and a reference location, or shares the
   device location.
2. The application proposes a cultivated-area polygon from real geospatial context. The farmer can
   move vertices or edit coordinates and must explicitly confirm the boundary.
3. A requested analysis is created idempotently and sent through Cloud Tasks to a private worker.
4. The worker searches real Sentinel-2 L2A observations through Earth Search, reads only the bounded
   COG windows needed for the field, applies scale/offset metadata, aligns the bands, and masks cloud,
   shadow, cirrus, snow, invalid, and no-data pixels.
5. Deterministic code calculates NDVI, NDRE, and NDMI across time and groups the field into two to
   seven relative-development zones.
6. The web application displays the field map, zone areas, trajectories, scene dates, cloud cover,
   provenance, and downloadable GeoJSON.
7. A Gemini 3.5+ ADK coordinator delegates questions to boundary, temporal-analysis, and evidence
   specialists. The specialists use narrow repository tools and a private MCP catalog service.
8. When explicitly asked, the temporal specialist can request another analysis. Repeated model calls
   safely replay the same action instead of creating duplicates.
9. The farmer can request another zone grouping and record whether the explanation was helpful,
   unclear, or not helpful. That feedback is persisted against the exact analysis and agent session.

The application supports Portuguese and English. It accepts text and supported-browser voice input,
but all answers are returned as readable text with a compact trace showing the model, agent, and tools
used.

## Why this is a Collaborative Partner

The partner leads a four-step workflow rather than waiting for an expert prompt. It asks one
clarifying question at a time, preserves the farmer's field and season context, requires correction or
confirmation of the boundary, adapts the output to the requested number of zones, and captures
feedback. The farmer remains the decision-maker; Gemini converts complex evidence into a safe next
step.

This is also active data work, not retrieval-only chat. The agent can initiate an idempotent analysis,
and the system transforms heterogeneous satellite metadata, multispectral rasters, cloud masks, field
geometry, and user corrections into new persisted evidence and GeoJSON artifacts.

## How we built it

### Gemini and Google agent technology

- **Gemini 3.5+ through Vertex AI** performs intent understanding, delegation, tool selection, and
  grounded explanation.
- **Google Agent Development Kit** defines one coordinator and three specialized agents.
- **MCP over Streamable HTTP** exposes an allowlisted, read-only Sentinel catalog surface to the
  boundary and temporal specialists.
- Typed repository tools provide compact field, analysis, zone, and provenance evidence without
  sending raster arrays through the model context.
- One narrowly scoped action tool lets the temporal specialist request an analysis after explicit
  user intent and confirmed geometry.

### Google Cloud

- **Cloud Run** hosts four independently permissioned roles from one immutable container image: the
  public Django web service, private Sentinel worker, private MCP service, and private ADK service.
- **Vertex AI** provides Gemini to the ADK service through its Cloud Run identity.
- **Firestore** stores fields, analyses, sessions, feedback, usage counters, and idempotency records.
- **Cloud Storage** stores generated previews and GeoJSON artifacts.
- **Cloud Tasks** executes long-running analyses asynchronously with authenticated delivery, retries,
  and one-at-a-time processing for the demo.
- **Secret Manager**, IAM service accounts, Artifact Registry, and Cloud Build support deployment
  without long-lived service-account keys.

### Deterministic geospatial processing

The worker uses Python, Rasterio, NumPy, Shapely, and scikit-learn. It searches Sentinel-2 L2A scenes,
reads B04, B05, B08, B11, and SCL, aligns every observation to one canonical grid, computes spectral
indices, and applies deterministic spatially smoothed clustering. The exact scene identifiers,
calibration metadata, timestamps, geometry, and processing scope are preserved in the result.

The architecture keeps model reasoning separate from numerical measurement: Gemini cannot fabricate
or modify a pixel result. It receives a compact evidence envelope only after deterministic processing.

## Data sources

- Sentinel-2 L2A scene metadata is discovered through the public Earth Search STAC API maintained by
  Element 84.
- Sentinel-2 Cloud Optimized GeoTIFF assets are read from the AWS Open Data archive.
- The demonstration uses only the family-authorized field named `[AUTHORIZED_FIELD_NAME]`.
- The project does not use the previously discussed academic TCC field, its private imagery, or its
  results.

Dataset reference:
[Sentinel-2 Cloud-Optimized GeoTIFFs — Registry of Open Data on AWS](https://registry.opendata.aws/sentinel-2-l2a-cogs/).
Sensor reference:
[ESA Sentinel-2 multispectral instrument](https://www.esa.int/Our_Activities/Observing_the_Earth/Copernicus/Sentinel-2/Instrument).

## Challenges

The most difficult engineering issue was preserving scientific and operational boundaries across an
agentic workflow. Satellite observations can be incomplete, cloudy, spread across adjacent tiles,
calibrated differently, or expressed on different grids. At the same time, an LLM can make an
uncertain signal sound more conclusive than it is.

We addressed those issues by:

- treating same-day adjacent tiles as alternatives rather than independent temporal evidence;
- requiring at least two usable observations with all required bands;
- applying catalog-provided calibration and explicit SCL masking;
- reprojecting before comparison and keeping every raster read bounded;
- persisting strict provenance and refusing to invent unavailable evidence;
- separating deterministic measurement from Gemini explanation;
- making every mutation idempotent and every private service IAM-authenticated.

## Accomplishments

- A working end-to-end path from field context to real multispectral zone evidence.
- A Google ADK multi-agent application that uses Gemini 3.5+, grounded repository tools, MCP, and a
  real action tool.
- Reproducible Google Cloud infrastructure with four least-privilege Cloud Run services.
- An interface usable on a phone, in Portuguese or English, with explicit field-boundary control.
- Scientific guardrails that keep the output useful without converting it into a diagnosis.

## Findings and learnings

One local real-data validation completed in approximately 42 seconds and returned three usable scenes
and two relative-development zones. This is a **local validation observation**, not a claim about
general accuracy, agronomic cause, or guaranteed production latency. Final submission evidence will
come from the recorded run and its matching persisted manifest.

We also validated locally that the ADK, Gemini, and MCP path can work together. The main architectural
learning was that the best use of Gemini here is not to replace remote-sensing algorithms. It is to
select safe operations, retain conversational context, ask for missing decisions, and translate exact
evidence into an understandable next action.

## What is intentionally out of scope

The MVP does not diagnose pests, disease, nutrient deficiency, soil condition, irrigation need,
yield, or treatment. It does not establish ownership or legal field boundaries. It does not claim
that saved feedback retrains the model. Its output identifies relative differences inside one field
and supports field inspection and local agronomic judgment.

## What's next

The next stage would add agronomist-reviewed feedback policies, persistent conversational memory,
field-observation notes, drone imagery as an optional second-resolution layer, and longitudinal
evaluation across multiple authorized farms. Those additions would be evaluated against ground truth;
they are not represented as capabilities of this submission.

## Testing instructions

1. Open `[FINAL_HOSTED_URL]`.
2. Sign in with the demonstration credentials supplied in the Devpost testing instructions.
3. Choose English from the language selector if desired.
4. Use the authorized demonstration field or enter a permitted location and season.
5. Review the suggested polygon, confirm it, and start the analysis.
6. Keep the page open while it polls the persisted analysis state.
7. When results appear, inspect the zones and evidence table, ask Gemini a question, and open the
   execution trace.
8. Use the feedback controls and download the GeoJSON if desired.

The hosted project is provided free of charge for judging. Reproducible local and Cloud deployment
instructions are in the repository README and `infra/gcp/README.md`.

## Development-period and assistance disclosure

The submitted project was created during the official August 3–31, 2026 submission period; its public
Git history records that work. Standard open-source libraries, public data services, and AI coding
assistants were used during implementation. No pre-existing commercial product code or results from
the unrelated TCC were incorporated into the submission.

## Official contest references

- [Official rules](https://allthingsagentichackathon.devpost.com/rules)
- [Official challenge overview](https://allthingsagentichackathon.devpost.com/)
- [Official FAQ](https://allthingsagentichackathon.devpost.com/details/faqs)
