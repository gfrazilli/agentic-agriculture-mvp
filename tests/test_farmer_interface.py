from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse

from tests.conftest import TEST_PASSWORD, TEST_USERNAME


class _SemanticHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.elements.append((tag, dict(attrs)))

    def attributes_for_id(self, element_id: str) -> tuple[str, dict[str, str | None]]:
        for tag, attributes in self.elements:
            if attributes.get("id") == element_id:
                return tag, attributes
        raise AssertionError(f"Expected an element with id={element_id!r}")


def _login(client) -> None:
    response = client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 302


def _authenticated_home(client) -> tuple[str, _SemanticHtmlParser]:
    _login(client)
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert [template.name for template in response.templates if template.name] == [
        "core/home.html",
        "base.html",
    ]

    html = response.content.decode()
    parser = _SemanticHtmlParser()
    parser.feed(html)
    return html, parser


@pytest.mark.parametrize(
    ("asset", "url"),
    [
        ("core/farmer-app.css", "/static/core/farmer-app.css"),
        ("core/farmer-app.js", "/static/core/farmer-app.js"),
    ],
)
def test_authenticated_farmer_interface_loads_versioned_static_assets(client, asset, url):
    html, _ = _authenticated_home(client)

    assert url in html
    assert finders.find(asset) is not None


def test_farmer_interface_exposes_four_step_form_and_contract_fields(client):
    _, parser = _authenticated_home(client)

    form_tag, form_attributes = parser.attributes_for_id("field-form")
    assert form_tag == "form"
    assert form_attributes.get("novalidate") is not None

    step_numbers = {
        attributes["data-step"]
        for _, attributes in parser.elements
        if attributes.get("data-step") is not None
    }
    assert step_numbers == {"1", "2", "3", "4"}

    field_names = {
        attributes["name"]
        for _, attributes in parser.elements
        if attributes.get("name") is not None
    }
    assert {
        "name",
        "crop",
        "season_start",
        "season_end",
        "estimated_area_ha",
        "latitude",
        "longitude",
    } <= field_names


def test_farmer_interface_configures_only_real_versioned_api_routes(client):
    html, _ = _authenticated_home(client)

    expected_routes = (
        "/api/v1/fields/",
        "boundary-suggestions/",
        "/api/v1/analyses/",
        "/api/v1/agent-sessions/",
        "/api/v1/feedback/",
        "/api/v1/fixtures/field-draft/",
        "/api/v1/fixtures/boundary-suggestion/",
        "/api/v1/fixtures/analysis-running/",
        "/api/v1/fixtures/analysis-result/",
    )
    for route in expected_routes:
        assert route in html

    assert "/api/v1/lavoura/detalhes/" not in html


def test_map_progress_and_results_are_accessible_regions(client):
    _, parser = _authenticated_home(client)

    map_tag, boundary_map = parser.attributes_for_id("boundary-map")
    assert map_tag in {"div", "svg"}
    assert boundary_map.get("role") == "img"
    assert boundary_map.get("aria-label") or boundary_map.get("aria-labelledby")

    _, progress = parser.attributes_for_id("analysis-progress")
    assert progress.get("role") == "progressbar"
    assert progress.get("aria-valuemin") == "0"
    assert progress.get("aria-valuemax") == "100"
    assert progress.get("aria-live") in {"polite", "assertive"}

    results_tag, results = parser.attributes_for_id("analysis-results")
    assert results_tag in {"section", "div"}
    assert results.get("role") == "region"
    assert results.get("aria-label") or results.get("aria-labelledby")

    for element_id in ("zone-controls", "gemini-assistant", "feedback-panel"):
        tag, _ = parser.attributes_for_id(element_id)
        assert tag == "section"


def test_farmer_interface_renders_in_portuguese_and_english(client):
    _login(client)
    portuguese = client.get(reverse("home"))
    assert portuguese.status_code == 200
    assert '<html lang="pt-br">' in portuguese.content.decode()
    assert "Cadastre sua lavoura" in portuguese.content.decode()

    switch = client.post(
        reverse("set_language"),
        {"language": "en", "next": reverse("home")},
    )
    assert switch.status_code == 302

    english = client.get(switch.url)
    assert english.status_code == 200
    assert '<html lang="en">' in english.content.decode()
    assert "Register your field" in english.content.decode()


def test_anonymous_farmer_is_redirected_to_the_demo_login(client):
    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next=%2F"


def test_farmer_script_uses_real_contract_and_safe_dom_updates():
    script_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "farmer-app.js"
    script = script_path.read_text(encoding="utf-8")

    assert "/api/v1/lavoura/detalhes/" not in script
    assert "innerHTML" not in script

    for field_name in (
        "name",
        "crop",
        "season_start",
        "season_end",
        "estimated_area_ha",
        "reference_location",
        "boundary_confirmed",
        "requested_zone_count",
    ):
        assert field_name in script

    for header_name in ("Content-Type", "X-CSRFToken", "Idempotency-Key"):
        assert header_name in script

    assert "boundary-suggestions" in script
    assert "analyses" in script
    assert "agentSessionsUrl" in script
    assert "recluster/" in script
    assert "feedback" in script
    assert "SpeechRecognition" in script
    assert 'format: "plain_text"' not in script
    assert 'boundary.type === "Polygon"' in script
    assert 'boundary.type === "MultiPolygon"' in script
    assert '"fill-rule": "evenodd"' in script
    assert "zoneGeometryPath(zone.boundary, projection)" in script


def test_language_switch_restores_the_persisted_workflow_from_the_api():
    script_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "farmer-app.js"
    script = script_path.read_text(encoding="utf-8")

    assert 'workflowRestoreKey = "agentic-agriculture:language-switch:v1"' in script
    assert 'document.querySelector(".language-switcher")?.addEventListener(' in script
    assert '"submit", saveWorkflowForLanguageSwitch' in script
    assert "window.sessionStorage.setItem(workflowRestoreKey" in script
    assert "window.sessionStorage.removeItem(workflowRestoreKey)" in script
    assert "apiRequest(`${copy.fieldsUrl}${saved.fieldId}/`)" in script
    assert "apiRequest(`${copy.analysesUrl}${saved.analysisId}/`)" in script
    assert 'if (analysis.status === "completed")' in script
    assert "renderResult(analysis)" in script
    assert "void restoreWorkflowAfterLanguageSwitch()" in script


def test_agent_interface_keeps_private_cloud_identity_out_of_the_browser():
    script_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "farmer-app.js"
    template_path = Path(settings.BASE_DIR) / "templates" / "core" / "home.html"
    browser_source = script_path.read_text(encoding="utf-8") + template_path.read_text(
        encoding="utf-8"
    )

    assert "AGENT_API_URL" not in browser_source
    assert "AGENT_API_AUDIENCE" not in browser_source
    assert "Authorization" not in browser_source
    assert "identity token" not in browser_source.lower()


def test_obsolete_donor_files_are_not_left_at_repository_root():
    repository_root = Path(settings.BASE_DIR)

    for filename in ("CSS UI.css", "ScriptUI.js", "UI Onboarding.html"):
        assert not (repository_root / filename).exists()
