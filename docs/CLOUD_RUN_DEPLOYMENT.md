# Cloud Run deployment guide

PR4-PR7 use one container image with four Cloud Run service roles. This keeps dependencies
and source revisions identical while allowing separate commands, identities, scaling, and IAM.

## Services

| Service | Container command | Exposure |
| --- | --- | --- |
| Web | Image default (`gunicorn`) | Public demo URL; agricultural routes still require login |
| Sentinel worker | Image default with `WEB_CONCURRENCY=1`, `GUNICORN_THREADS=1`, and `GUNICORN_TIMEOUT=840` | Private; invoked only by Cloud Tasks |
| MCP | `python -m geospatial.mcp_server` | Private, callable only by the ADK service account |
| Agent | `adk api_server --host 0.0.0.0 --port 8080 --session_service_uri=memory:// --artifact_service_uri=gs://BUCKET agentic_agriculture` | Private or protected by an authenticated gateway |

The ADK development API server does not provide application-level authentication. Cloud Run
IAM (or an authenticated gateway) is therefore mandatory; do not deploy it with unauthenticated
invocation enabled. `memory://` sessions are suitable only for a single-instance demo. Use a
persistent ADK session service before enabling multiple agent instances. Agricultural evidence
itself remains durable in Firestore and Cloud Storage.

## Google Cloud resources

Enable Cloud Run, Cloud Build or Artifact Registry, Firestore, Cloud Storage, Cloud Tasks,
Vertex AI, and IAM Credentials APIs. Create:

- one regional Cloud Tasks queue;
- one private Cloud Storage bucket for generated artifacts;
- a Firestore database;
- separate web, worker, MCP, and agent service accounts;
- Secret Manager values for Django, demo-login, and internal-task secrets.

Recommended least-privilege direction:

- web: Firestore access, task enqueue, and permission to invoke the private worker through the
  Cloud Tasks service account;
- worker: Firestore access, object access to the artifact bucket, and permission to receive
  authenticated tasks;
- MCP: Cloud Run runtime only; its tools access the public Earth Search catalog;
- agent: read-only Firestore access, Vertex AI user, and Cloud Run invoker on the MCP service;
- Cloud Tasks caller: Cloud Run invoker on the worker service.

## Runtime configuration

Use the production variables listed in the root `README.md`, including all Google backends,
`BOUNDARY_BACKEND=geospatial` and `ANALYSIS_PIPELINE_BACKEND=sentinel`. The trusted public Earth
Search STAC endpoint is fixed in code. For the agent service:

```text
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=<project-id>
GOOGLE_CLOUD_LOCATION=global
DJANGO_SETTINGS_MODULE=config.settings
AGENT_MODEL=gemini-3.5-flash
AGENT_MCP_ENABLED=true
AGENT_MCP_URL=https://<private-mcp-service>.run.app/mcp
AGENT_MCP_AUDIENCE=https://<private-mcp-service>.run.app
```

The audience is the service origin, not the `/mcp` path. The agent lazily obtains a Google ID
token for that audience and sends it to the MCP service. Do not store a static bearer token.

Point `CLOUD_TASKS_BASE_URL` at the private worker, set its Cloud Run request timeout and
`CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS` to 900 seconds, and keep its Gunicorn timeout at 840
seconds. Configure the queue's retry window above 20 minutes. Active duplicates receive 503;
after a worker crash, retries resume once the 20-minute lease becomes stale.

## Verification before the demo

1. Confirm `/healthz` and authenticated `/readyz` behavior on the web service.
2. Submit one field and verify that a Cloud Task completes with a persisted analysis result.
3. Inspect scene IDs, dates, scale/offset metadata, coverage, and the generated GeoJSON.
4. From the agent identity, call the private MCP service; verify that an unauthenticated call
   is denied.
5. Ask Gemini for the observations and zone differences in Portuguese and confirm that it
   cites the stored evidence without claiming a pest, disease, nutrient, soil, or water cause.
6. Keep one previously completed field cached for a reliable recorded and live demonstration.
