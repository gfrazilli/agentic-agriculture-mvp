# Final capture report

Capture date: 2026-08-31 (America/Sao_Paulo)

## Demonstrated field

- Name: `Taquarituba Family Field`
- Crop: soybeans
- Monitoring period: 2025-10-15 through 2026-03-15
- Farmer estimate: 36 hectares
- Guided demonstration: off
- Real boundary proposal: 30.3 hectares at 74% confidence
- Precise coordinates are intentionally excluded from this report and the final video.

## Real Sentinel-2 result

- Four relative-development areas
- Five usable Sentinel-2 L2A observations
- Dates: 2025-10-15, 2025-11-01, 2025-11-19, 2026-02-22, 2026-03-14
- Indices: NDVI, NDRE, NDMI
- Source shown in the UI: EU/ESA/Copernicus via Earth Search/AWS Open Data
- Bands shown in the UI: B04, B05, B08, B11, SCL
- Processing version shown in the UI: 0.3.0

The initial workflow reached the result within the measured 93-second capture window. The 40-second
processing segment shows chronological checkpoints captured from that run, and the narration states
the measured runtime rather than implying instantaneous processing.

## Gemini evidence response

The accepted response:

- prioritizes Area 1;
- cites February 22 and March 14, 2026;
- cites NDVI, NDRE, and NDMI values;
- gives exactly three scouting steps;
- does not diagnose a cause;
- exposes `gemini-3.5-flash`, Google ADK, the coordinator/specialist agents, and evidence tools in the trace.

## Autonomous state change

- Request: create a new comparison with three areas without duplicating retries
- Analysis ID: `ed6d27ea-03c0-4c76-b3f5-8617e45b02a4`
- Agent event: `agent_action.request_field_analysis`
- Worker stages: acquisition, index computation, clustering, explanation, completion
- Final status: completed
- Requested area count: 3
- Selected Sentinel-2 scenes: 5
- Idempotent replay protection was stated by the agent and backed by the persisted analysis ID.

## Google Cloud proof

Project: `agentic-agriculture-2026`, region: `southamerica-east1`.

| Role | Ready revision |
| --- | --- |
| Web | `agentic-agriculture-web-00029-nqx` |
| Worker | `agentic-agriculture-worker-00029-zws` |
| MCP | `agentic-agriculture-mcp-00016-9r2` |
| Agent | `agentic-agriculture-agent-00016-759` |

All four roles use immutable application image `app:c27da853f549`.

## Privacy and integrity checks

- No credentials are present in captured assets.
- Precise coordinates are cropped from the edit.
- No billing page, email address, avatar, or unrelated browser tab is included.
- Guided demo stayed off.
- The result is described as a scouting priority map, not a diagnosis.
- Devpost submission is outside this production step and must not be performed automatically.

## Final media QA

- Runtime: 233.000 seconds (3:53)
- Video: H.264, 1920×1080, 30 fps, yuv420p
- Audio: AAC, 48 kHz, stereo, −16.4 LUFS integrated, −4.1 dBFS true peak
- Captions: 50 synchronized cues, at most two lines, burned in and exported as SRT
- Full-stream decode: passed with zero errors
- SHA-256: `5BFC4F777B38B537D5938CCF90E32A6BB1AC3D96E1FE4378174AC6984F6C70A1`
