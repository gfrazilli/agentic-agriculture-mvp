from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from agriculture.container import reset_container
from tests.conftest import TEST_PASSWORD, TEST_USERNAME


@pytest.fixture(autouse=True)
def isolated_agriculture_container() -> Iterator[None]:
    reset_container()
    yield
    reset_container()


@pytest.fixture
def api_client(client: Client) -> Client:
    response = client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 302
    return client


def _field_payload(*, name: str = "Talhão Norte") -> dict[str, object]:
    return {
        "name": name,
        "crop": "soja",
        "season_start": "2025-10-15",
        "season_end": "2026-03-10",
        "estimated_area_ha": 12.4,
        "reference_location": {
            "type": "Point",
            "coordinates": [-48.9029, -23.9786],
        },
    }


def _json_request(
    client: Client,
    method: str,
    url: str,
    payload: dict[str, object],
    *,
    idempotency_key: str | None = None,
):
    headers = {}
    if idempotency_key is not None:
        headers["HTTP_IDEMPOTENCY_KEY"] = idempotency_key
    return getattr(client, method.lower())(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def _create_field(client: Client, *, key: str = "field-create-001") -> dict[str, object]:
    response = _json_request(
        client,
        "post",
        reverse("agriculture_api:fields"),
        _field_payload(),
        idempotency_key=key,
    )
    assert response.status_code == 201, response.content
    return response.json()["data"]


def _suggest_and_confirm_boundary(
    client: Client,
    field: dict[str, object],
    *,
    key: str = "boundary-create-001",
) -> dict[str, object]:
    suggestion_response = _json_request(
        client,
        "post",
        reverse("agriculture_api:boundary-suggestion", args=[field["id"]]),
        {},
        idempotency_key=key,
    )
    assert suggestion_response.status_code == 201, suggestion_response.content
    suggestion = suggestion_response.json()["data"]
    confirmation = _json_request(
        client,
        "patch",
        reverse("agriculture_api:field-detail", args=[field["id"]]),
        {"boundary": suggestion["boundary"], "boundary_confirmed": True},
    )
    assert confirmation.status_code == 200, confirmation.content
    return confirmation.json()["data"]


def _create_confirmed_field(client: Client, *, suffix: str = "001") -> dict[str, object]:
    field = _create_field(client, key=f"field-create-{suffix}")
    return _suggest_and_confirm_boundary(client, field, key=f"boundary-create-{suffix}")


def test_anonymous_api_returns_json_401(client: Client) -> None:
    response = client.get(reverse("agriculture_api:fields"))

    assert response.status_code == 401
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "schema_version": "1.0",
        "error": {
            "code": "authentication_required",
            "message": "Authentication is required.",
        },
    }


def test_invalid_uuid_route_returns_versioned_json_404(api_client: Client, settings) -> None:
    settings.DEBUG = False

    response = api_client.get("/api/v1/fields/not-a-uuid/")

    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "schema_version": "1.0",
        "error": {
            "code": "not_found",
            "message": "The API endpoint or resource was not found.",
        },
    }


def test_field_create_list_get_patch_and_boundary_confirmation(api_client: Client) -> None:
    field = _create_field(api_client)
    assert field["schema_version"] == "1.0"
    assert field["boundary"] is None
    assert field["boundary_confirmed"] is False

    listed = api_client.get(reverse("agriculture_api:fields"))
    fetched = api_client.get(reverse("agriculture_api:field-detail", args=[field["id"]]))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["data"]] == [field["id"]]
    assert fetched.status_code == 200
    assert fetched.json()["data"] == field

    renamed = _json_request(
        api_client,
        "patch",
        reverse("agriculture_api:field-detail", args=[field["id"]]),
        {"name": "Talhão Norte Revisado"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["name"] == "Talhão Norte Revisado"

    confirmed = _suggest_and_confirm_boundary(api_client, field)
    assert confirmed["boundary_confirmed"] is True
    assert confirmed["boundary"]["type"] == "Polygon"
    assert confirmed["boundary"]["coordinates"][0][0] == confirmed["boundary"]["coordinates"][0][-1]


def test_analysis_requires_confirmed_boundary_then_returns_202(api_client: Client) -> None:
    field = _create_field(api_client)
    analyses_url = reverse("agriculture_api:analyses")
    rejected = _json_request(
        api_client,
        "post",
        analyses_url,
        {"field_id": field["id"], "requested_zone_count": 4},
        idempotency_key="analysis-before-confirm",
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "field_boundary_not_confirmed"

    _suggest_and_confirm_boundary(api_client, field)
    accepted = _json_request(
        api_client,
        "post",
        analyses_url,
        {"field_id": field["id"], "requested_zone_count": 4},
        idempotency_key="analysis-after-confirm",
    )
    assert accepted.status_code == 202
    analysis = accepted.json()["data"]
    assert analysis["status"] == "queued"
    assert analysis["progress"]["percent"] == 0
    assert analysis["requested_zone_count"] == 4

    fetched = api_client.get(reverse("agriculture_api:analysis-detail", args=[analysis["id"]]))
    assert fetched.status_code == 200
    assert fetched.json()["data"] == analysis


def test_idempotency_replays_same_body_and_rejects_changed_body(api_client: Client) -> None:
    url = reverse("agriculture_api:fields")
    key = "same-field-request"
    first = _json_request(
        api_client,
        "post",
        url,
        _field_payload(),
        idempotency_key=key,
    )
    replay = _json_request(
        api_client,
        "post",
        url,
        _field_payload(),
        idempotency_key=key,
    )
    conflict = _json_request(
        api_client,
        "post",
        url,
        _field_payload(name="Outro talhão"),
        idempotency_key=key,
    )

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"


def test_analysis_daily_quota_is_three_and_sets_retry_after(api_client: Client) -> None:
    field = _create_confirmed_field(api_client, suffix="quota")
    url = reverse("agriculture_api:analyses")
    payload = {"field_id": field["id"], "requested_zone_count": 4}

    for number in range(1, 4):
        response = _json_request(
            api_client,
            "post",
            url,
            payload,
            idempotency_key=f"analysis-quota-{number}",
        )
        assert response.status_code == 202, response.content

    limited = _json_request(
        api_client,
        "post",
        url,
        payload,
        idempotency_key="analysis-quota-4",
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "daily_analysis_limit_exceeded"
    assert int(limited.headers["Retry-After"]) > 0


def test_fixture_index_and_each_validated_fixture_are_available(api_client: Client) -> None:
    index = api_client.get(reverse("agriculture_api:fixtures"))
    assert index.status_code == 200
    assert index.json()["data"]["fixtures"] == [
        "field-draft",
        "boundary-suggestion",
        "analysis-running",
        "analysis-result",
    ]

    for name in index.json()["data"]["fixtures"]:
        response = api_client.get(reverse("agriculture_api:fixture-detail", args=[name]))
        assert response.status_code == 200
        assert response.json()["data"]["schema_version"] == "1.0"

    missing = api_client.get(reverse("agriculture_api:fixture-detail", args=["does-not-exist"]))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "fixture_not_found"


def test_agent_session_patch_and_feedback(api_client: Client) -> None:
    field = _create_confirmed_field(api_client, suffix="agent")
    analysis_response = _json_request(
        api_client,
        "post",
        reverse("agriculture_api:analyses"),
        {"field_id": field["id"], "requested_zone_count": 4},
        idempotency_key="agent-analysis-001",
    )
    analysis = analysis_response.json()["data"]

    session_response = _json_request(
        api_client,
        "post",
        reverse("agriculture_api:agent-sessions"),
        {
            "language": "pt-BR",
            "channel": "voice",
            "field_id": field["id"],
            "analysis_id": analysis["id"],
        },
        idempotency_key="agent-session-001",
    )
    assert session_response.status_code == 201
    session = session_response.json()["data"]
    assert session["channel"] == "voice"
    assert session["turn_count"] == 0

    patched = _json_request(
        api_client,
        "patch",
        reverse("agriculture_api:agent-session-detail", args=[session["id"]]),
        {"increment_turn_count": True},
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["turn_count"] == 1

    feedback = _json_request(
        api_client,
        "post",
        reverse("agriculture_api:feedback"),
        {
            "analysis_id": analysis["id"],
            "session_id": session["id"],
            "rating": "unclear",
            "comment": "Quero uma explicação mais simples.",
        },
        idempotency_key="agent-feedback-001",
    )
    assert feedback.status_code == 201
    assert feedback.json()["data"]["rating"] == "unclear"
    assert feedback.json()["data"]["analysis_id"] == analysis["id"]


def test_invalid_content_type_json_object_and_body_size(api_client: Client) -> None:
    url = reverse("agriculture_api:fields")
    wrong_type = api_client.post(
        url,
        data=json.dumps(_field_payload()),
        content_type="text/plain",
        HTTP_IDEMPOTENCY_KEY="wrong-type-001",
    )
    malformed = api_client.post(
        url,
        data=b'{"name":',
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="bad-json-001",
    )
    array_body = api_client.post(
        url,
        data=b"[]",
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="array-json-001",
    )
    with override_settings(API_MAX_REQUEST_BYTES=10, DATA_UPLOAD_MAX_MEMORY_SIZE=10):
        oversized = _json_request(
            api_client,
            "post",
            url,
            _field_payload(),
            idempotency_key="oversized-001",
        )

    assert wrong_type.status_code == 415
    assert wrong_type.json()["error"]["code"] == "unsupported_media_type"
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "invalid_json"
    assert array_body.status_code == 400
    assert array_body.json()["error"]["code"] == "invalid_json_object"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request_too_large"


def test_method_contract_and_missing_idempotency_key(api_client: Client) -> None:
    fields_url = reverse("agriculture_api:fields")
    wrong_method = api_client.put(
        fields_url,
        data=b"{}",
        content_type="application/json",
    )
    missing_key = _json_request(api_client, "post", fields_url, _field_payload())

    assert wrong_method.status_code == 405
    assert wrong_method.headers["Allow"] == "GET, POST"
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "idempotency_key_required"


def test_mutating_api_requires_csrf() -> None:
    csrf_client = Client(enforce_csrf_checks=True)
    login_page = csrf_client.get(reverse("login"))
    assert login_page.status_code == 200
    token = csrf_client.cookies["csrftoken"].value
    logged_in = csrf_client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
        HTTP_X_CSRFTOKEN=token,
    )
    assert logged_in.status_code == 302

    rejected = _json_request(
        csrf_client,
        "post",
        reverse("agriculture_api:fields"),
        _field_payload(),
        idempotency_key="csrf-field-001",
    )
    accepted = csrf_client.post(
        reverse("agriculture_api:fields"),
        data=json.dumps(_field_payload()),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="csrf-field-002",
        HTTP_X_CSRFTOKEN=token,
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 201
