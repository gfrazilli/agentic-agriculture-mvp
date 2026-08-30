import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra" / "gcp"


def _read(name: str) -> str:
    return (INFRA / name).read_text(encoding="utf-8")


def test_shell_scripts_are_valid_bash():
    if sys.platform == "win32":
        # Windows resolves bash to the optional WSL launcher. Git Bash syntax
        # is exercised separately during local validation; CI runs real Bash.
        return
    bash = shutil.which("bash")
    if bash is None:
        return

    for name in ("bootstrap.sh", "budget.sh", "custom-domain.sh", "deploy.sh", "smoke.sh"):
        subprocess.run(
            [bash, "-n", str(INFRA / name)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_bootstrap_provisions_private_regional_dependencies_and_oidc_iam():
    script = _read("bootstrap.sh")

    for api in (
        "aiplatform.googleapis.com",
        "artifactregistry.googleapis.com",
        "cloudbuild.googleapis.com",
        "cloudtasks.googleapis.com",
        "firestore.googleapis.com",
        "run.googleapis.com",
        "secretmanager.googleapis.com",
    ):
        assert api in script
    assert "southamerica-east1" in script
    assert "--public-access-prevention" in script
    assert "--uniform-bucket-level-access" in script
    assert "--delete-protection" in script
    assert "roles/cloudtasks.enqueuer" in script
    assert script.count("roles/iam.serviceAccountUser") >= 2
    assert "TASKS_SERVICE_AGENT" in script
    assert "roles/storage.objectUser" in script
    assert "roles/aiplatform.user" in script
    assert 'add_project_role "serviceAccount:${AGENT_SA}" roles/datastore.user' in script
    assert 'add_project_role "serviceAccount:${AGENT_SA}" roles/cloudtasks.enqueuer' in script
    assert '"$TASK_INVOKER_SA" "serviceAccount:${AGENT_SA}" roles/iam.serviceAccountUser' in script
    assert 'add_secret_role "$TASK_SECRET_ID" "$AGENT_SA"' in script
    assert "--data-file=-" in script


def test_deploy_uses_one_pinned_image_and_only_web_is_public():
    script = _read("deploy.sh")

    assert script.count('--image="$IMAGE_URI"') == 4
    assert script.count("--no-allow-unauthenticated") == 3
    assert script.count("--invoker-iam-check") == 3
    assert script.count("--no-invoker-iam-check") == 1
    assert ":latest" not in script
    assert "DJANGO_SECRET_VERSION" in script
    assert "DEMO_PASSWORD_SECRET_VERSION" in script
    assert "TASK_SECRET_VERSION" in script
    assert '--region="$REGION"' in script
    assert "roles/run.invoker" in script
    assert "MSYS2_ARG_CONV_EXCL" in script


def test_worker_and_agent_limits_match_the_runtime_contract():
    script = _read("deploy.sh")

    worker_block = script.split('gcloud run deploy "$WORKER_SERVICE"', 1)[1].split(
        'gcloud run deploy "$MCP_SERVICE"', 1
    )[0]
    assert "--concurrency=1" in worker_block
    assert "--max-instances=1" in worker_block
    assert "--timeout=900" in worker_block
    assert "GUNICORN_TIMEOUT=840" in worker_block
    assert "tcpSocket.port=8080" in worker_block

    agent_block = script.split('gcloud run deploy "$AGENT_SERVICE"', 1)[1].split(
        'gcloud run deploy "$WEB_SERVICE"', 1
    )[0]
    assert "--max-instances=1" in agent_block
    assert "--session_service_uri=memory://" in agent_block
    assert "--auto_create_session" in agent_block
    assert "AGENT_MCP_URL=${MCP_URL}/mcp" in script
    assert "AGENT_MCP_AUDIENCE=${MCP_URL}" in script
    assert "TASK_BACKEND=cloud_tasks" in script
    assert "ARTIFACT_BACKEND=gcs" in script
    assert "GCS_BUCKET=${ARTIFACT_BUCKET}" in script
    assert "CLOUD_TASKS_BASE_URL=${WORKER_URL}" in script
    assert "CLOUD_TASKS_SERVICE_ACCOUNT=${TASK_INVOKER_SA}" in script
    assert "CLOUD_TASKS_SHARED_SECRET=${TASK_SECRET_ID}:${TASK_SECRET_VERSION}" in agent_block


def test_web_is_the_only_gateway_allowed_to_invoke_the_private_agent():
    script = _read("deploy.sh")

    web_block = script.split('gcloud run deploy "$WEB_SERVICE"', 1)[1]
    assert "AGENT_API_URL=${AGENT_URL}" in script
    assert "AGENT_API_AUDIENCE=${AGENT_URL}" in script
    assert "AGENT_API_TIMEOUT_SECONDS=120" in script
    assert 'add_run_invoker "$AGENT_SERVICE" "serviceAccount:${WEB_SA}"' in script
    assert "--no-invoker-iam-check" in web_block


def test_custom_domains_are_strictly_validated_and_only_added_to_the_web_revision():
    script = _read("deploy.sh")

    assert 'CUSTOM_DOMAINS_RAW="${AA_CUSTOM_DOMAINS:-}"' in script
    assert "validate_hostname" in script
    assert "AA_CUSTOM_DOMAINS cannot contain whitespace" in script
    assert "AA_CUSTOM_DOMAINS contains a duplicate hostname" in script
    assert 'WEB_ALLOWED_HOSTS+=",${custom_domain}"' in script
    assert 'WEB_CSRF_TRUSTED_ORIGINS+=",https://${custom_domain}"' in script
    assert "DJANGO_ALLOWED_HOSTS=${WEB_ALLOWED_HOSTS}" in script
    assert "DJANGO_CSRF_TRUSTED_ORIGINS=${WEB_CSRF_TRUSTED_ORIGINS}" in script

    private_runtime_block = script.split('log "Deploying the private Sentinel worker."', 1)[
        1
    ].split('log "Deploying the public, login-protected web service."', 1)[0]
    assert "WEB_ALLOWED_HOSTS" not in private_runtime_block
    assert "WEB_CSRF_TRUSTED_ORIGINS" not in private_runtime_block


def test_custom_domain_reconciles_the_expected_non_destructive_global_alb():
    script = _read("custom-domain.sh")

    for resource_name in (
        "aa-web-ip",
        "aa-web-neg",
        "aa-web-backend",
        "aa-web-url-map",
        "aa-web-cert",
        "aa-web-https-proxy",
        "aa-web-https",
        "aa-web-http-proxy",
        "aa-web-http",
    ):
        assert resource_name in script

    assert '[[ -n "$PROJECT_ID" ]]' in script
    assert '[[ -n "$CUSTOM_DOMAIN" ]]' in script
    assert "validate_hostname" in script
    assert "validate_ipv4" in script
    assert "validate_resource_name" in script
    assert "--network-endpoint-type=serverless" in script
    assert '--cloud-run-service="$WEB_SERVICE"' in script
    assert script.count("--load-balancing-scheme=EXTERNAL") >= 2
    assert "gcloud compute ssl-certificates create" in script
    assert '--domains="${CUSTOM_DOMAIN},${CUSTOM_WWW_DOMAIN}"' in script
    assert "gcloud compute target-https-proxies create" in script
    assert "gcloud compute target-http-proxies create" in script
    assert 'ensure_forwarding_rule "$HTTPS_RULE_NAME" 443' in script
    assert 'ensure_forwarding_rule "$HTTP_RULE_NAME" 80' in script
    assert "Certificate status: ${CERT_STATUS}" in script
    assert "DNS was not modified" in script
    assert "gcloud compute addresses delete" not in script
    assert "gcloud compute backend-services delete" not in script
    assert "api.cloudflare.com" not in script.lower()
    assert "CLOUDFLARE_API_TOKEN" not in script
    assert "curl " not in script


def test_custom_domain_runbook_covers_dns_only_provisioning_and_cost_boundary():
    runbook = _read("README.md")

    assert "AA_CUSTOM_DOMAIN=1415agri.com" in runbook
    assert "AA_CUSTOM_DOMAINS" in runbook
    assert "bash infra/gcp/custom-domain.sh" in runbook
    assert "136.110.164.101" in runbook
    assert "DNS only" in runbook
    assert "PROVISIONING" in runbook
    assert "southamerica-east1" in runbook
    assert "Cloud Run domain mapping is not used" in runbook
    assert "ongoing forwarding-rule and data-processing charges" in runbook


def test_budget_is_scoped_and_deployment_upload_excludes_local_secrets():
    budget = _read("budget.sh")
    ignore = (ROOT / ".gcloudignore").read_text(encoding="utf-8")

    assert "--filter-projects=projects/$PROJECT_ID" in budget
    assert "--threshold-rule=percent=0.50" in budget
    assert "--threshold-rule=percent=1.00,basis=forecasted-spend" in budget
    assert "#!include:.gitignore" in ignore
    assert "gha-creds-*.json" in ignore


def test_smoke_accepts_cloud_run_private_route_hiding():
    script = _read("smoke.sh")

    assert 'status_code" == "404"' in script
    assert "service_is_public" in script
    assert "run.googleapis.com/invoker-iam-disabled" in script
    assert '"$WEB_URL/login/"' in script


def test_infrastructure_never_creates_long_lived_keys_or_embeds_secret_values():
    scripts = "\n".join(
        _read(name)
        for name in ("bootstrap.sh", "budget.sh", "custom-domain.sh", "deploy.sh", "smoke.sh")
    )

    assert "service-accounts keys create" not in scripts
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in scripts
    assert "GEMINI_API_KEY=" not in scripts
    assert "--set-secrets" in scripts
    assert "DJANGO_SECRET_KEY=${DJANGO_SECRET_ID}:" in scripts
    assert "CLOUD_TASKS_SHARED_SECRET=${TASK_SECRET_ID}:" in scripts
