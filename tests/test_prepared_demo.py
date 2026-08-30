from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from django.urls import reverse

from agriculture.container import get_repository, reset_container
from agriculture.fixture_loader import load_fixture
from config.settings import env_optional_uuid_pair
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_agriculture_container() -> Iterator[None]:
    reset_container()
    yield
    reset_container()


@pytest.fixture
def authenticated_client(client: Client) -> Client:
    response = client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 302
    assert response.url == reverse("demo")
    return client


def _configure_prepared_demo(settings, field_id: UUID, analysis_id: UUID) -> None:
    settings.PREPARED_DEMO_FIELD_ID = field_id
    settings.PREPARED_DEMO_ANALYSIS_ID = analysis_id


def _assert_safe_unavailable(response, *identifiers: UUID) -> None:
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.json()
    assert payload["error"] == {
        "code": "prepared_demo_unavailable",
        "message": "The prepared demonstration is unavailable.",
    }
    body = response.content.decode()
    for identifier in identifiers:
        assert str(identifier) not in body


def test_prepared_demo_requires_authentication(client, settings):
    settings.PREPARED_DEMO_FIELD_ID = None
    settings.PREPARED_DEMO_ANALYSIS_ID = None

    response = client.get(reverse("agriculture_api:prepared-demo"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_prepared_demo_returns_completed_cached_field_and_analysis(
    authenticated_client,
    settings,
):
    field = load_fixture("field-draft")
    analysis = load_fixture("analysis-result")
    repository = get_repository()
    repository.save_field(field)
    repository.save_analysis(analysis)
    _configure_prepared_demo(settings, field.id, analysis.id)

    response = authenticated_client.get(reverse("agriculture_api:prepared-demo"))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()["data"]
    assert data["prepared"] is True
    assert data["field"]["id"] == str(field.id)
    assert data["analysis"]["id"] == str(analysis.id)
    assert data["analysis"]["field_id"] == data["field"]["id"]
    assert data["analysis"]["status"] == "completed"
    assert data["analysis"]["result"] is not None


def test_prepared_demo_is_unavailable_when_not_configured(authenticated_client, settings):
    settings.PREPARED_DEMO_FIELD_ID = None
    settings.PREPARED_DEMO_ANALYSIS_ID = None

    response = authenticated_client.get(reverse("agriculture_api:prepared-demo"))

    _assert_safe_unavailable(response)


@pytest.mark.parametrize(
    "repository_state",
    ("missing_field", "missing_analysis", "wrong_field", "not_completed"),
)
def test_prepared_demo_fails_closed_for_invalid_cached_state(
    authenticated_client,
    settings,
    repository_state,
):
    field = load_fixture("field-draft")
    completed = load_fixture("analysis-result")
    repository = get_repository()
    configured_field_id = field.id
    configured_analysis_id = completed.id

    if repository_state != "missing_field":
        repository.save_field(field)
    if repository_state == "missing_analysis":
        configured_analysis_id = uuid4()
    elif repository_state == "wrong_field":
        completed = completed.model_copy(update={"field_id": uuid4()})
        repository.save_analysis(completed)
    elif repository_state == "not_completed":
        running = load_fixture("analysis-running")
        repository.save_analysis(running)
    else:
        repository.save_analysis(completed)

    _configure_prepared_demo(settings, configured_field_id, configured_analysis_id)
    response = authenticated_client.get(reverse("agriculture_api:prepared-demo"))

    _assert_safe_unavailable(response, configured_field_id, configured_analysis_id)


def test_prepared_demo_accepts_only_get(authenticated_client, settings):
    settings.PREPARED_DEMO_FIELD_ID = None
    settings.PREPARED_DEMO_ANALYSIS_ID = None

    response = authenticated_client.post(reverse("agriculture_api:prepared-demo"))

    assert response.status_code == 405
    assert response.headers["Allow"] == "GET"
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_prepared_demo_uuid_settings_are_optional_as_a_pair(monkeypatch):
    first_id = uuid4()
    second_id = uuid4()
    monkeypatch.setenv("TEST_PREPARED_FIELD", str(first_id))
    monkeypatch.setenv("TEST_PREPARED_ANALYSIS", str(second_id))

    assert env_optional_uuid_pair("TEST_PREPARED_FIELD", "TEST_PREPARED_ANALYSIS") == (
        first_id,
        second_id,
    )

    monkeypatch.delenv("TEST_PREPARED_FIELD")
    monkeypatch.delenv("TEST_PREPARED_ANALYSIS")
    assert env_optional_uuid_pair("TEST_PREPARED_FIELD", "TEST_PREPARED_ANALYSIS") == (
        None,
        None,
    )


@pytest.mark.parametrize(
    ("field_value", "analysis_value"),
    ((str(uuid4()), ""), ("", str(uuid4())), ("not-a-uuid", str(uuid4()))),
)
def test_prepared_demo_uuid_settings_fail_fast(monkeypatch, field_value, analysis_value):
    monkeypatch.setenv("TEST_PREPARED_FIELD", field_value)
    monkeypatch.setenv("TEST_PREPARED_ANALYSIS", analysis_value)

    with pytest.raises(ImproperlyConfigured):
        env_optional_uuid_pair("TEST_PREPARED_FIELD", "TEST_PREPARED_ANALYSIS")


def test_farmer_interface_prefers_prepared_demo_and_limits_fixture_fallback():
    template = (ROOT / "templates" / "core" / "home.html").read_text(encoding="utf-8")
    script = (ROOT / "core" / "static" / "core" / "farmer-app.js").read_text(encoding="utf-8")

    assert 'data-prepared-demo-url="/api/v1/demo/prepared/"' in template
    assert "preparedDemo = await apiRequest(copy.preparedDemoUrl)" in script
    assert "error.status === 404" in script
    assert 'error.code === "prepared_demo_unavailable"' in script
    assert "if (!preparedDemoUnavailable) throw error" in script
    assert "state.field = preparedDemo.field" in script
    assert "state.analysis = preparedDemo.analysis" in script
    assert "state.persistedAnalysis = preparedDemo.analysis" in script
    assert "state.guidedResult = false" in script
    assert "renderResult(preparedDemo.analysis)" in script
    assert script.index("copy.preparedDemoUrl") < script.index("copy.fixtureFieldUrl")


def test_deploy_injects_prepared_demo_ids_only_into_the_web_service():
    script = (ROOT / "infra" / "gcp" / "deploy.sh").read_text(encoding="utf-8")
    worker_block = script.split('gcloud run deploy "$WORKER_SERVICE"', 1)[1].split(
        'gcloud run deploy "$MCP_SERVICE"', 1
    )[0]
    mcp_block = script.split('gcloud run deploy "$MCP_SERVICE"', 1)[1].split(
        'gcloud run deploy "$AGENT_SERVICE"', 1
    )[0]
    agent_block = script.split('gcloud run deploy "$AGENT_SERVICE"', 1)[1].split(
        'log "Deploying the public, login-protected web service."', 1
    )[0]
    web_block = script.split('log "Deploying the public, login-protected web service."', 1)[1]

    assert "must be set together" in script
    assert "Prepared demonstration IDs must be valid UUID values." in script
    assert "AA_PREPARED_DEMO_FIELD_ID=${PREPARED_DEMO_FIELD_ID}" in web_block
    assert "AA_PREPARED_DEMO_ANALYSIS_ID=${PREPARED_DEMO_ANALYSIS_ID}" in web_block
    for private_service_block in (worker_block, mcp_block, agent_block):
        assert "AA_PREPARED_DEMO_FIELD_ID=" not in private_service_block
        assert "AA_PREPARED_DEMO_ANALYSIS_ID=" not in private_service_block
