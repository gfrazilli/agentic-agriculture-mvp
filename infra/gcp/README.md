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
- six Secret Manager secrets, including Resend, Turnstile, and the private contact recipient available only to the web service;
- four independently scaled Cloud Run services from one immutable image tag;
- an optional global external classic Application Load Balancer for custom domains;
- an optional monthly billing budget alert scoped to this project.

Only the web service is public, and it still requires the application's demonstration login.
Worker, MCP, and ADK have public `run.app` origins so authenticated Google service-to-service
calls work without a VPC connector, but Cloud Run IAM denies unauthenticated invocation.
The web service account is the only browser-facing gateway allowed to invoke ADK. Django mints
a short-lived identity token server-side; neither that token nor the private agent origin is
sent to the browser.
The ADK identity can also enqueue the narrowly scoped, idempotent analysis action after the
farmer confirms a field boundary. It can write the analysis record and enqueue Cloud Tasks, but
the dedicated task-invoker identity remains the only caller allowed to execute the worker.

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
export CONTACT_RESEND_API_KEY_FILE="$PWD/infra/gcp/.secrets/resend-api-key.txt"
export CONTACT_TURNSTILE_SECRET_KEY_FILE="$PWD/infra/gcp/.secrets/turnstile-secret-key.txt"
export CONTACT_TO_EMAIL_FILE="$PWD/infra/gcp/.secrets/contact-recipient.txt"
export AA_CONTACT_TURNSTILE_SITE_KEY="replace-with-the-public-site-key"
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
requires the demo password hash, Resend API key, Turnstile secret, and private contact
recipient through their `*_FILE` (preferred) or `*_VALUE` variables. Secret values are piped
to Secret Manager over standard input and are never printed. Existing versions are preserved
unless `AA_ROTATE_SECRETS=true` is set. The deploy requires
`AA_CONTACT_TURNSTILE_SITE_KEY`, which is public and is stored as a normal Cloud Run
environment variable.

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

## Custom domain through the global load balancer

Cloud Run domain mapping is not used for this deployment. The web service runs in
`southamerica-east1`, so the custom domain uses Google's supported global external classic
Application Load Balancer path instead:

```text
global IPv4
  -> global HTTP/HTTPS forwarding rules
  -> global target HTTP/HTTPS proxies
  -> global URL map and Google-managed certificate
  -> global external backend service
  -> regional serverless NEG
  -> public Cloud Run web service
```

The current production names are intentionally stable:

| Role | Google Cloud resource |
| --- | --- |
| Static IPv4 | `aa-web-ip` (`136.110.164.101`) |
| Serverless NEG | `aa-web-neg` |
| Backend service | `aa-web-backend` |
| URL map | `aa-web-url-map` |
| Managed certificate | `aa-web-cert` |
| HTTPS proxy and rule | `aa-web-https-proxy`, `aa-web-https` |
| HTTP proxy and rule | `aa-web-http-proxy`, `aa-web-http` |

Set the Django host allowlist before deploying the web revision, then reconcile the load
balancer. `AA_CUSTOM_WWW_DOMAIN` defaults to `www.$AA_CUSTOM_DOMAIN`:

```bash
export GCP_PROJECT_ID=agentic-agriculture-2026
export GCP_REGION=southamerica-east1
export AA_CUSTOM_DOMAIN=1415agri.com
export AA_CUSTOM_WWW_DOMAIN=www.1415agri.com
export AA_CUSTOM_EXPECTED_IP=136.110.164.101
export AA_CUSTOM_DOMAINS="$AA_CUSTOM_DOMAIN,$AA_CUSTOM_WWW_DOMAIN"

bash infra/gcp/deploy.sh
bash infra/gcp/custom-domain.sh
```

`custom-domain.sh` is a non-destructive reconciler. It creates missing resources, reuses
compatible resources, and stops if a named resource points at an unexpected service, backend,
certificate, proxy, port, or address. It never deletes or replaces a resource and never calls
Cloudflare.

Create these records in Cloudflare using **DNS only** (gray cloud), not proxied, while the
Google-managed certificate is being issued:

```text
A  1415agri.com      136.110.164.101
A  www.1415agri.com  136.110.164.101
```

Certificate issuance is asynchronous; `PROVISIONING` is expected until DNS has propagated and
Google validates both hostnames. Rerun `custom-domain.sh` to print `managed.status` and the
per-domain status. Keep the records DNS-only at least until the certificate is `ACTIVE`.
Enabling Cloudflare proxying later changes the TLS termination and can interfere with Google's
certificate renewal; that configuration is outside these scripts and should be evaluated
separately.

The load balancer has ongoing forwarding-rule and data-processing charges even when every Cloud
Run service is scaled to zero. The reserved global IPv4 address can also be billable. The
Google-managed certificate itself does not remove those load-balancer costs. Review current
[Cloud Load Balancing pricing](https://cloud.google.com/vpc/network-pricing#lb) before leaving
the custom-domain infrastructure active after the demonstration. The budget alert reports spend
but does not stop these resources automatically.

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
- [Serverless NEGs with external Application Load Balancers](https://cloud.google.com/load-balancing/docs/negs/serverless-neg-concepts)
- [Google-managed SSL certificates](https://cloud.google.com/load-balancing/docs/ssl-certificates/google-managed-certs)
