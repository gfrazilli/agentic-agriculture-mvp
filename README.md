# Agentic Agriculture MVP

Google Cloud-ready Django and Google ADK application for the Agentic Agriculture MVP. It
combines public Sentinel-2 imagery, deterministic multispectral processing, MCP tools, and
Gemini 3.5+ agents while preserving a stable interface contract for the independently built
PR3A web UI.

## Scope

This repository contains the application foundation plus the PR4-PR7 backend stack:

- Python 3.12 and Django 5.2;
- one demonstration account configured entirely through environment variables;
- Django signed-cookie sessions, with no relational session table;
- protected `/`, login, POST-only logout, language selection, and static assets;
- strict Pydantic contracts for fields, boundaries, analyses, agent sessions, and feedback;
- authenticated `/api/v1/` endpoints with idempotency, validation, and daily analysis limits;
- stable example payloads for the beginner-friendly PR3A interface;
- Firestore persistence, Cloud Storage artifact, and Cloud Tasks queue adapters;
- an authenticated Cloud Tasks worker that runs the Sentinel pipeline;
- public Sentinel-2 catalog and COG access through Earth Search on AWS;
- georeferenced boundary suggestions with an explicit low-confidence fallback;
- calibrated NDVI, NDRE, and NDMI time-series analysis with deterministic 2-7-zone clustering;
- read-only MCP tools for the geospatial catalog plus grounded repository tools for evidence;
- a Portuguese Google ADK multi-agent application using Gemini 3.5+;
- deterministic in-memory adapters for automated tests and local prototyping;
- public liveness and readiness endpoints;
- Gunicorn + WhiteNoise production runtime;
- pytest, Ruff, Docker/Compose, and GitHub Actions.

The PR3A interface remains deliberately independent. No template, stylesheet, browser script,
or UI fixture was changed by PR4-PR7. Example fixtures remain interface contracts and are not
presented as results from a real field.

## Data storage

Yes, the application has a production database. Agricultural records are stored in
**Firestore**: fields, analyses, agent sessions, feedback, idempotency claims, and daily
usage counters each have their own collection. Larger imagery and generated artifacts use
**Cloud Storage**, while **Cloud Tasks** coordinates asynchronous work.

The `DATABASES` entry in Django is intentionally a dummy relational backend only because the
single demonstration login uses a signed cookie and does not need SQL tables. It does not
mean agricultural data is kept in cookies or discarded. Local development defaults to a
process-local memory adapter; `APP_ENV=production` fails immediately unless Firestore,
Cloud Storage, and Cloud Tasks are selected.

## Quick start with Docker

Requirements: Docker with Compose.

1. Copy `.env.example` to `.env`.
2. Replace `DJANGO_SECRET_KEY` with a long random value.
3. Generate a password hash (the password itself is never placed in `.env`):

   ```bash
   docker run --rm python:3.12-slim sh -c \
     "pip install 'Django>=5.2,<5.3' >/dev/null && python -c \"from django.conf import settings; settings.configure(); from django.contrib.auth.hashers import make_password; print(make_password('choose-a-password'))\""
   ```

4. Put that entire output in `DEMO_PASSWORD_HASH`. Keep it inside single quotes because
   Django hashes contain `$` characters.
5. Start the service:

   ```bash
   docker compose up --build
   ```

Open <http://localhost:8080/login/> and use `DEMO_USERNAME` plus the password chosen in
step 3. The Compose health check calls `/readyz`, so the container remains unhealthy until
the demonstration credentials are valid.

## Local development

Python 3.12 is required. On PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Django does not automatically read `.env` files. Export the values in your shell or use a
local environment loader of your choice. At minimum, set `DEMO_USERNAME` and a valid
`DEMO_PASSWORD_HASH`; generate the latter after installing dependencies:

```powershell
python -c "from django.conf import settings; settings.configure(); from django.contrib.auth.hashers import make_password; print(make_password('choose-a-password'))"
```

Then run:

```powershell
python manage.py runserver
```

To inspect the ADK application locally without calling Gemini or MCP during startup:

```powershell
$env:AGENT_MCP_ENABLED="false"
$env:DJANGO_SETTINGS_MODULE="config.settings"
adk api_server --host 127.0.0.1 --port 8001 --session_service_uri=memory:// --artifact_service_uri=memory:// agentic_agriculture
```

Open <http://127.0.0.1:8001/docs> or call `GET /list-apps`. A model turn requires valid
Vertex AI credentials and `GOOGLE_CLOUD_PROJECT`.

Compiled PT-BR messages are included in the repository. After editing `locale/**/*.po`,
run `python manage.py compilemessages` with GNU gettext installed. The Docker build does
this automatically.

## Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `APP_ENV` | No | `development` by default; `production` enables secure defaults. |
| `DJANGO_SECRET_KEY` | Production | Signs cookies and CSRF data; keep stable across replicas. |
| `DJANGO_ALLOWED_HOSTS` | Production | Comma-separated hostnames, such as `service-xyz.run.app`. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | For custom origins | Comma-separated HTTPS origins including the scheme. |
| `DJANGO_DEBUG` | No | Defaults to false in production. Never enable in production. |
| `PRODUCT_NAME` | No | Product label displayed in the UI. |
| `DEMO_USERNAME` | Yes for readiness | Username for the single demonstration account. |
| `DEMO_PASSWORD_HASH` | Yes for readiness | Encoded Django password hash, never a plain password. |
| `PERSISTENCE_BACKEND` | Production | `memory` locally or `firestore` in production. |
| `ARTIFACT_BACKEND` | Production | `memory` locally or `gcs` in production. |
| `TASK_BACKEND` | Production | `memory` locally or `cloud_tasks` in production. |
| `GOOGLE_CLOUD_PROJECT` | Google backends | Google Cloud project ID. |
| `FIRESTORE_DATABASE` | No | Firestore database; defaults to `(default)`. |
| `GCS_BUCKET` | GCS | Bucket for imagery and generated artifacts. |
| `CLOUD_TASKS_LOCATION` | Cloud Tasks | Queue region. |
| `CLOUD_TASKS_QUEUE` | Cloud Tasks | Queue name. |
| `CLOUD_TASKS_BASE_URL` | Cloud Tasks | HTTPS base URL of the Cloud Run service. |
| `CLOUD_TASKS_SERVICE_ACCOUNT` | Recommended | Service account used for OIDC task calls. |
| `CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS` | Cloud Tasks | Worker request deadline; defaults to 900 and must be 60-1800. |
| `CLOUD_TASKS_SHARED_SECRET` | Cloud Tasks | Random 32+ character secret used only for internal task delivery. |
| `BOUNDARY_BACKEND` | Production | `fixture` locally or real `geospatial` suggestions. |
| `ANALYSIS_PIPELINE_BACKEND` | Production | `disabled` locally or the real `sentinel` pipeline. |
| `ANALYSIS_TARGET_SCENE_COUNT` | No | Target number of observations; must be 2-12. |
| `ANALYSIS_MAX_DIMENSION` | No | Maximum processing grid dimension; must be 64-1024 pixels. |
| `AGENT_MODEL` | No | Gemini model; defaults to `gemini-3.5-flash` and rejects versions below 3.5. |
| `AGENT_MCP_ENABLED` | No | Enables the agent's read-only MCP toolsets. |
| `AGENT_MCP_URL` | MCP | Streamable HTTP URL of the private MCP service. |
| `AGENT_MCP_AUDIENCE` | Private Cloud Run MCP | Service origin used to mint the Google ID token. |
| `GOOGLE_GENAI_USE_VERTEXAI` | Agent | Set to `true` for Vertex AI-backed ADK execution. |
| `GOOGLE_CLOUD_LOCATION` | Agent | Vertex AI location; defaults to `global`. |
| `ANALYSIS_DAILY_LIMIT` | No | New analyses/regroupings per browser/day; defaults to 3. |
| `API_MAX_REQUEST_BYTES` | No | JSON body ceiling; defaults to 256 KiB. |
| `DJANGO_TIME_ZONE` | No | Defaults to `America/Sao_Paulo`. |
| `DJANGO_SESSION_COOKIE_AGE` | No | Maximum session age in seconds; defaults to 8 hours. |
| `DJANGO_COOKIE_SECURE` | No | Defaults to true in production. |
| `DJANGO_SECURE_SSL_REDIRECT` | No | Defaults to true in production. |
| `DJANGO_SECURE_HSTS_SECONDS` | No | Defaults to one year in production and zero locally. |
| `PORT` | No | HTTP port; defaults to `8080`, as expected by Cloud Run. |
| `WEB_CONCURRENCY` | No | Gunicorn workers; use 1 with memory, 2+ with Firestore. |
| `GUNICORN_THREADS` | No | Threads per worker; defaults to 4. |
| `GUNICORN_TIMEOUT` | No | Worker timeout; defaults to 840 seconds for Sentinel tasks. |

Production startup fails if `DJANGO_SECRET_KEY` or `DJANGO_ALLOWED_HOSTS` is absent, or if an
in-memory agriculture backend is selected. The readiness probe returns `503` if credentials
or the selected backend configuration is incomplete, so a Cloud Run restart cannot silently
switch field or analysis data to memory. Readiness validates configuration; it deliberately
does not add a remote Firestore/Storage/Tasks round trip to every health probe.

The in-memory repository is process-local. Keep `WEB_CONCURRENCY=1` while using it; use the
Firestore emulator for persistent local integration work. Firestore production can use
multiple workers and Cloud Run instances safely.

## Routes

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/` | Protected home page. |
| `GET`, `POST` | `/login/` | Demonstration login; POST is CSRF-protected. |
| `POST` | `/logout/` | Clears the signed session. |
| `POST` | `/i18n/setlang/` | Persists PT-BR or English in Django's language cookie. |
| `GET` | `/healthz` | Cheap liveness response, independent of auth and persistence. |
| `GET` | `/readyz` | Validates the minimum demonstration credential configuration. |
| `POST` | `/internal/tasks/analyses` | Authenticated delivery contract for Cloud Tasks. |

### API v1

All API routes require the demonstration session and return JSON envelopes with
`schema_version: "1.0"`. POST operations require an `Idempotency-Key` header. Reusing a key
with the same body atomically creates at most one resource and replays the original result;
reusing it with a different body returns 409.

| Method | Route | Description |
| --- | --- | --- |
| `GET`, `POST` | `/api/v1/fields/` | List or create crop fields. |
| `GET`, `PATCH` | `/api/v1/fields/{id}/` | Read or edit a field and confirm its boundary. |
| `POST` | `/api/v1/fields/{id}/boundary-suggestions/` | Return the PR2 suggestion contract. |
| `POST` | `/api/v1/analyses/` | Queue an analysis for a confirmed field. |
| `GET` | `/api/v1/analyses/{id}/` | Read analysis state/result. |
| `POST` | `/api/v1/analyses/{id}/recluster/` | Queue a 2-7-zone regrouping. |
| `POST` | `/api/v1/agent-sessions/` | Start a voice or text agent session. |
| `GET`, `PATCH` | `/api/v1/agent-sessions/{id}/` | Read or update agent-session context. |
| `POST` | `/api/v1/feedback/` | Record farmer feedback. |
| `GET` | `/api/v1/fixtures/` | List stable PR3A fixture names. |
| `GET` | `/api/v1/fixtures/{name}/` | Fetch and validate a stable fixture. |

## Quality checks

```bash
ruff check .
ruff format --check .
pytest
python manage.py check
docker build -t agentic-agriculture-mvp .
```

Tests intentionally run without `django_db`: PR2 uses repository ports rather than Django
ORM models. The in-memory adapter and Firestore adapter share the same contracts; local
persistent integration testing can use the Firestore emulator.

## Cloud Run

The reproducible infrastructure lives in [`infra/gcp`](infra/gcp/README.md). Its idempotent
scripts provision Firestore, Cloud Storage, Cloud Tasks, Artifact Registry, Secret Manager,
least-privilege service accounts, a monthly budget alert, and four Cloud Run services from one
commit-tagged image. Build and deploy the same `Dockerfile`. Configure at least these runtime
variables in the Cloud Run service:

```text
APP_ENV=production
DJANGO_SECRET_KEY=<managed secret>
DJANGO_ALLOWED_HOSTS=<service host>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<service host>
DEMO_USERNAME=<demo user>
DEMO_PASSWORD_HASH=<encoded Django hash>
PRODUCT_NAME=Agentic Agriculture
PERSISTENCE_BACKEND=firestore
ARTIFACT_BACKEND=gcs
TASK_BACKEND=cloud_tasks
GOOGLE_CLOUD_PROJECT=<project id>
GCS_BUCKET=<artifact bucket>
CLOUD_TASKS_LOCATION=<queue region>
CLOUD_TASKS_QUEUE=<queue name>
CLOUD_TASKS_BASE_URL=https://<private worker service host>
CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS=900
CLOUD_TASKS_SHARED_SECRET=<managed random secret of at least 32 characters>
```

The image runs as a non-root user, listens on `0.0.0.0:$PORT`, serves static assets through
WhiteNoise, and understands Cloud Run's `X-Forwarded-Proto` header. Prefer your cloud secret
manager for the Django secret, password hash, and internal task secret rather than plain
deployment arguments.

Deploy the task receiver as a dedicated private Cloud Run service with a 900-second request
timeout, `WEB_CONCURRENCY=1`, `GUNICORN_THREADS=1`, and `GUNICORN_TIMEOUT=840`. Its task deadline
is 900 seconds and its processing lease is 20 minutes. A delivery received while that lease is
active returns a retryable response; if a worker dies, a later retry resumes the analysis instead
of acknowledging and losing the task.

The internal receiver authenticates and validates Cloud Tasks deliveries, then runs the real
Sentinel pipeline when `ANALYSIS_PIPELINE_BACKEND=sentinel`. Development keeps the original
explicit `pipeline_implemented: false` response when the pipeline is disabled. The shared
secret is application-level authentication; `X-CloudTasks-TaskName` is required delivery
metadata but is not treated as a secret.

Deploy the web API, Sentinel worker, MCP server, and ADK API as four independently permissioned
Cloud Run services built from the same image. See [architecture](docs/ARCHITECTURE.md) and the
[Cloud Run deployment guide](docs/CLOUD_RUN_DEPLOYMENT.md).

## Security model and limitations

Signed-cookie sessions are integrity-protected but **not encrypted**. This application stores
only an authentication-version HMAC and an opaque random browser actor ID in the session
cookie—never the username, password, or configured password hash. Changing the username,
password hash, or Django secret key invalidates existing sessions.

This is deliberately a single-account demonstration gate, not a full identity system. It has
no centralized session revocation, user administration, audit history, or distributed login
rate limiting. Keep the session lifetime short, serve only over HTTPS, protect environment
secrets, and replace this mechanism with managed identity before opening the product to real
users.
