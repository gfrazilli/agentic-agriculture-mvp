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


def _authenticated_demo(client) -> tuple[str, _SemanticHtmlParser]:
    _login(client)
    response = client.get(reverse("demo"))
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
    html, _ = _authenticated_demo(client)

    assert url in html
    assert finders.find(asset) is not None


def test_farmer_interface_exposes_four_step_form_and_contract_fields(client):
    _, parser = _authenticated_demo(client)

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
        "estimated_area",
        "area_unit",
        "latitude",
        "longitude",
    } <= field_names


def test_farmer_interface_offers_locale_aware_area_units_and_only_supported_crops(
    client,
):
    _, parser = _authenticated_demo(client)

    input_tag, area_input = parser.attributes_for_id("estimated-area")
    select_tag, area_unit = parser.attributes_for_id("area-unit")
    assert input_tag == "input"
    assert area_input["name"] == "estimated_area"
    assert area_input["min"] == "0.1"
    assert area_input["max"] == "500"
    assert area_input["step"] == "0.01"
    assert select_tag == "select"
    assert area_unit["name"] == "area_unit"
    assert area_unit.get("aria-label") == "Area unit"

    options = {
        attributes["value"]: attributes
        for tag, attributes in parser.elements
        if tag == "option" and attributes.get("value") is not None
    }
    assert "disabled" not in options["soja"]
    assert "disabled" not in options["milho"]
    for crop in ("cafe", "cana-de-acucar", "algodao", "trigo", "arroz", "outra"):
        assert "disabled" in options[crop]


def test_farmer_interface_configures_only_real_versioned_api_routes(client):
    html, _ = _authenticated_demo(client)

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
    _, parser = _authenticated_demo(client)

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
    english = client.get(reverse("demo"))
    assert english.status_code == 200
    assert '<html lang="en">' in english.content.decode()
    assert "Add a field" in english.content.decode()
    assert "Coffee (soon)" in english.content.decode()

    switch = client.post(
        reverse("set_language"),
        {"language": "pt-br", "next": reverse("demo")},
    )
    assert switch.status_code == 302

    portuguese = client.get(switch.url)
    assert portuguese.status_code == 200
    assert '<html lang="pt-br">' in portuguese.content.decode()
    portuguese_content = portuguese.content.decode()
    assert "Adicione uma área" in portuguese_content
    assert "Café (em breve)" in portuguese_content
    assert "Sua área plantada não é toda igual" in portuguese_content
    assert "talhão" not in portuguese_content.lower()
    assert "zona" not in portuguese_content.lower()


def test_anonymous_farmer_is_redirected_to_the_demo_login(client):
    response = client.get(reverse("demo"))

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next=%2Fdemo%2F"


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
    assert 'const locale = language === "pt" ? "pt-BR" : "en-US"' in script
    assert 'let selectedAreaUnit = language === "pt" ? "hectares" : "acres"' in script
    assert 'soybean: "soja"' in script
    assert 'corn: "milho"' in script
    assert "displayAreaToHectares(areaInput.value, unit)" in script
    assert "state.areaHectares" in script
    assert "state.areaDisplayValue" in script
    assert 'areaInput.min = usesAcres ? "0.2471" : "0.1"' in script
    assert 'areaInput.max = usesAcres ? "1235.53" : "500"' in script
    assert 'areaInput.step = usesAcres ? "any" : "0.01"' in script
    assert "new Intl.NumberFormat(locale" in script
    assert "smallAreaNumberFormatter" in script
    assert "new Intl.DateTimeFormat(locale" in script
    assert "area_acres:" in script
    assert 'download: "1415-agri-field-areas.geojson"' in script


def test_language_switch_restores_the_persisted_workflow_from_the_api():
    script_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "farmer-app.js"
    script = script_path.read_text(encoding="utf-8")

    assert 'workflowRestoreKey = "agentic-agriculture:language-switch:v1"' in script
    assert 'document.querySelector(".language-switcher")?.addEventListener(' in script
    assert '"submit", saveWorkflowForLanguageSwitch' in script
    assert "window.sessionStorage.setItem(workflowRestoreKey" in script
    assert "window.sessionStorage.removeItem(workflowRestoreKey)" in script
    assert 'mode: "guided"' in script
    assert 'mode: "boundary"' in script
    assert 'mode: "analysis"' in script
    assert "boundaryForLanguageSwitch()" in script
    assert "isValidSavedBoundary(saved.boundary, saved.fieldId)" in script
    assert 'guided: app.querySelector("#guided-demo").checked' in script
    assert 'typeof saved.guided === "boolean"' in script
    assert 'app.querySelector("#guided-demo").checked = saved.guided' in script
    assert "apiRequest(copy.fixtureResultUrl)" in script
    assert "state.boundary = saved.boundary" in script
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
