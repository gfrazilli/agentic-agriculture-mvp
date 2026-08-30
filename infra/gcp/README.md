# Google Cloud infrastructure

These scripts provision and deploy the hackathon MVP without Terraform, long-lived JSON keys,
or secrets committed to Git. They are designed for Google Cloud Shell and also work in Git Bash
when the Google Cloud CLI is installed.

## What is created

- one private Artifact Registry Docker repository;
- one private, regional Cloud Storage bucket with uniform access;
- one delete-protected Firestore Native database;
- one rate-limited Cloud Tasks queue;
- separate identities for web, worker, MCP, ADK, and task invocation;
- three Secret Manager secrets;
- four independently scaled Cloud Run services from one immutable image tag;
- an optional monthly billing budget alert scoped to this project.

Only the web service is public, and it still requires the application's demonstration login.
Worker, MCP, and ADK have public `run.app` origins so authenticated Google service-to-service
calls work without a VPC connector, but Cloud Run IAM denies unauthenticated invocation.
The web service account is the only browser-facing gateway allowed to invoke ADK. Django mints
a short-lived identity token server-side; neither that token nor the private agent origin is
sent to the browser.

## First deployment

Authenticate the CLI and select a billed project:

```bash
gcloud auth login
gcloud config set project agentic-agriculture-2026
export GCP_PROJECT_ID=agentic-agriculture-2026
export GCP_REGION=southamerica-east1
```

Prepare only the encoded Django password hash. The plaintext password is never sent to the
bootstrap script or stored in Google Cloud:

```bash
mkdir -p infra/gcp/.secrets
python - <<'PY' > infra/gcp/.secrets/demo-password-hash.txt
from getpass import getpass
from django.conf import settings

settings.configure()
from django.contrib.auth.hashers import make_password

password = getpass("Demo password: ")
confirmation = getpass("Repeat password: ")
if password != confirmation or len(password) < 12:
    raise SystemExit("Passwords must match and contain at least 12 characters.")
print(make_password(password))
PY
```

Run the four stages from the repository root:

```bash
export DEMO_PASSWORD_HASH_FILE="$PWD/infra/gcp/.secrets/demo-password-hash.txt"
bash infra/gcp/bootstrap.sh
bash infra/gcp/budget.sh
bash infra/gcp/deploy.sh
bash infra/gcp/smoke.sh
```

The default monthly alert is `100` in the billing account currency. Override it with
`AA_MONTHLY_BUDGET_AMOUNT`. A standard Google Cloud budget sends notifications and does not
hard-stop usage; the deployment also keeps every service at `min-instances=0`, caps each
service's maximum instances, and limits the Sentinel queue to one concurrent dispatch.

The bootstrap creates random Django and internal-task secrets when they do not exist. It
requires the demo password hash through `DEMO_PASSWORD_HASH_FILE` or
`DEMO_PASSWORD_HASH_VALUE`. Secret values are piped to Secret Manager over standard input and
are never printed. Existing versions are preserved unless `AA_ROTATE_SECRETS=true` is set.

## Windows Git Bash

The Windows SDK installation includes an extensionless `gcloud` launcher. In Git Bash, point
it at the bundled Python before running the scripts:

```bash
export CLOUDSDK_PYTHON="/c/Users/giova/AppData/Local/Google/Cloud SDK/google-cloud-sdk/platform/bundledpython/python.exe"
export PATH="/c/Users/giova/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin:$PATH"
```

## Repeat deployment or rollback

`deploy.sh` tags the image with the current 12-character Git commit. A later run is safe: long-
lived resources are reused and service configuration is reconciled. To deploy an existing
image without rebuilding it:

```bash
export AA_SKIP_BUILD=true
export AA_IMAGE_URI="southamerica-east1-docker.pkg.dev/$GCP_PROJECT_ID/agentic-agriculture/app:<tag>"
bash infra/gcp/deploy.sh
```

The same mechanism rolls all four roles back to one earlier image. The deploy script discovers
the canonical worker and MCP URLs, uses the worker origin as the Cloud Tasks OIDC audience, and
uses the MCP origin (without `/mcp`) as the ADK token audience.

## Verification

`smoke.sh` verifies resource locations and privacy, IAM exposure, queue state, bucket controls,
service identities, timeouts, scaling, the shared image digest, public liveness/readiness, and
authenticated worker/MCP/ADK calls. Set `AA_SKIP_AUTHENTICATED_SMOKE=true` only when the active
operator is intentionally not allowed to invoke private services.

The HTTP probes use `/live` and `/ready`. Cloud Run reserves some paths ending in `z`, so the
deployment deliberately avoids conventional names such as `/healthz`.

Useful official references:

- [Cloud Run service identity](https://cloud.google.com/run/docs/securing/service-identity)
- [Cloud Run known issues and reserved paths](https://cloud.google.com/run/docs/known-issues)
- [Cloud Run service-to-service authentication](https://cloud.google.com/run/docs/authenticating/service-to-service)
- [Cloud Tasks authenticated HTTP targets](https://cloud.google.com/tasks/docs/creating-http-target-tasks)
- [Firestore database management](https://cloud.google.com/firestore/docs/manage-databases)
- [Cloud Run Secret Manager integration](https://cloud.google.com/run/docs/configuring/services/secrets)
- [Cloud Billing budgets](https://cloud.google.com/billing/docs/how-to/budgets)
