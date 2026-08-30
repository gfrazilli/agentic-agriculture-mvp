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

    for name in ("bootstrap.sh", "budget.sh", "deploy.sh", "smoke.sh"):
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
        _read(name) for name in ("bootstrap.sh", "budget.sh", "deploy.sh", "smoke.sh")
    )

    assert "service-accounts keys create" not in scripts
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in scripts
    assert "GEMINI_API_KEY=" not in scripts
    assert "--set-secrets" in scripts
    assert "DJANGO_SECRET_KEY=${DJANGO_SECRET_ID}:" in scripts
    assert "CLOUD_TASKS_SHARED_SECRET=${TASK_SECRET_ID}:" in scripts
