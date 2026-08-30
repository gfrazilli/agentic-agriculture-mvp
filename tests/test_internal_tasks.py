import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from agriculture.adapters.tasks import CloudTasksQueue
from agriculture.checks import backend_configuration
from agriculture.container import get_repository, reset_container
from agriculture.fixture_loader import load_fixture
from agriculture.internal.security import CLOUD_TASK_NAME_HEADER, TASK_SECRET_HEADER
from agriculture.schemas import Analysis
from geospatial.pipeline import PipelineOutcome

TASK_SECRET = "task-secret-for-tests-with-at-least-32-characters"
TASK_NAME = "projects/demo/locations/us-central1/queues/analysis/tasks/task-123"


@pytest.fixture(autouse=True)
def isolated_internal_task_container(settings):
    settings.PERSISTENCE_BACKEND = "memory"
    settings.CLOUD_TASKS_SHARED_SECRET = TASK_SECRET
    reset_container()
    yield
    reset_container()


@pytest.fixture
def stored_analysis() -> Analysis:
    analysis = load_fixture("analysis-running")
    assert isinstance(analysis, Analysis)
    return get_repository().save_analysis(analysis)


def _delivery_headers(*, secret: str = TASK_SECRET, include_task_name: bool = True) -> dict:
    headers = {"HTTP_X_INTERNAL_TASK_SECRET": secret}
    if include_task_name:
        headers["HTTP_X_CLOUDTASKS_TASKNAME"] = TASK_NAME
    return headers


def _payload(analysis: Analysis) -> dict[str, str | None]:
    return {
        "analysis_id": str(analysis.id),
        "field_id": str(analysis.field_id),
        "parent_analysis_id": (
            str(analysis.parent_analysis_id) if analysis.parent_analysis_id else None
        ),
    }


def _post(client: Client, payload: dict, **headers):
    return client.post(
        reverse("agriculture_internal:analysis-task"),
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def test_receiver_requires_configured_shared_secret(settings, client, stored_analysis):
    settings.CLOUD_TASKS_SHARED_SECRET = ""

    response = _post(client, _payload(stored_analysis), **_delivery_headers())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "task_authentication_unavailable"


@pytest.mark.parametrize("secret", ["", "wrong-secret-with-at-least-32-characters"])
def test_receiver_rejects_missing_or_wrong_secret(client, stored_analysis, secret):
    response = _post(client, _payload(stored_analysis), **_delivery_headers(secret=secret))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "task_authentication_failed"


def test_receiver_requires_cloud_tasks_delivery_header(client, stored_analysis):
    response = _post(
        client,
        _payload(stored_analysis),
        **_delivery_headers(include_task_name=False),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_task_delivery"


def test_valid_delivery_is_csrf_exempt_idempotent_and_explicitly_not_processed(
    stored_analysis,
):
    csrf_client = Client(enforce_csrf_checks=True)
    repository = get_repository()
    before = repository.get_analysis(str(stored_analysis.id))

    first = _post(csrf_client, _payload(stored_analysis), **_delivery_headers())
    replay = _post(csrf_client, _payload(stored_analysis), **_delivery_headers())
    after = repository.get_analysis(str(stored_analysis.id))

    expected = {
        "schema_version": "1.0",
        "data": {
            "analysis_id": str(stored_analysis.id),
            "outcome": "acknowledged_not_processed",
            "pipeline_implemented": False,
            "reason": "sentinel_gemini_pipeline_not_implemented",
        },
    }
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == expected
    assert first.headers["Cache-Control"] == "no-store"
    assert before == after == stored_analysis


def test_valid_delivery_runs_the_configured_sentinel_pipeline(
    monkeypatch,
    client,
    stored_analysis,
):
    calls: list[str] = []

    class FakePipeline:
        def run(self, analysis_id: str) -> PipelineOutcome:
            calls.append(analysis_id)
            return PipelineOutcome(
                analysis_id=analysis_id,
                status="completed",
                scene_count=4,
                zone_count=3,
            )

    monkeypatch.setattr(
        "agriculture.internal.views.get_analysis_pipeline",
        lambda: FakePipeline(),
    )

    response = _post(client, _payload(stored_analysis), **_delivery_headers())

    assert response.status_code == 200
    assert calls == [str(stored_analysis.id)]
    assert response.json()["data"] == {
        "analysis_id": str(stored_analysis.id),
        "outcome": "completed",
        "pipeline_implemented": True,
        "scene_count": 4,
        "zone_count": 3,
        "error_code": None,
        "retryable": False,
    }


def test_retryable_pipeline_failure_requests_cloud_tasks_retry(
    monkeypatch,
    client,
    stored_analysis,
):
    class FakePipeline:
        def run(self, analysis_id: str) -> PipelineOutcome:
            return PipelineOutcome(
                analysis_id=analysis_id,
                status="failed",
                error_code="EARTH_SEARCH_UNAVAILABLE",
                retryable=True,
            )

    monkeypatch.setattr(
        "agriculture.internal.views.get_analysis_pipeline",
        lambda: FakePipeline(),
    )

    response = _post(client, _payload(stored_analysis), **_delivery_headers())

    assert response.status_code == 503
    assert response.json()["data"]["retryable"] is True


def test_active_pipeline_lease_requests_cloud_tasks_retry(
    monkeypatch,
    client,
    stored_analysis,
):
    class FakePipeline:
        def run(self, analysis_id: str) -> PipelineOutcome:
            return PipelineOutcome(
                analysis_id=analysis_id,
                status="already_running",
                retryable=True,
            )

    monkeypatch.setattr(
        "agriculture.internal.views.get_analysis_pipeline",
        lambda: FakePipeline(),
    )

    response = _post(client, _payload(stored_analysis), **_delivery_headers())

    assert response.status_code == 503
    assert response.json()["data"]["outcome"] == "already_running"
    assert response.json()["data"]["retryable"] is True


def test_receiver_rejects_payload_that_does_not_match_stored_analysis(client, stored_analysis):
    payload = _payload(stored_analysis)
    payload["field_id"] = str(uuid4())

    response = _post(client, payload, **_delivery_headers())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_field_mismatch"


def test_receiver_rejects_unknown_analysis_and_extra_payload_fields(client, stored_analysis):
    unknown = _payload(stored_analysis)
    unknown["analysis_id"] = str(uuid4())
    not_found = _post(client, unknown, **_delivery_headers())

    invalid = _payload(stored_analysis)
    invalid["unexpected"] = True
    validation_error = _post(client, invalid, **_delivery_headers())

    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "task_analysis_not_found"
    assert validation_error.status_code == 422
    assert validation_error.json()["error"]["code"] == "validation_error"


def test_receiver_is_post_only(client):
    response = client.get(
        reverse("agriculture_internal:analysis-task"),
        **_delivery_headers(),
    )

    assert response.status_code == 405
    assert response.headers["Allow"] == "POST"


def test_cloud_tasks_adapter_sends_secret_only_in_authenticated_header(monkeypatch):
    import agriculture.adapters.tasks as task_adapter

    class FakeTimestamp:
        def FromDatetime(self, value):
            self.value = value

    class FakeDuration:
        def FromSeconds(self, value):
            self.seconds = value

    class FakeAlreadyExists(Exception):
        pass

    class FakeClient:
        def __init__(self):
            self.created_task = None

        def queue_path(self, project, location, queue):
            return f"projects/{project}/locations/{location}/queues/{queue}"

        def task_path(self, project, location, queue, task_id):
            return f"{self.queue_path(project, location, queue)}/tasks/{task_id}"

        def create_task(self, *, parent, task):
            self.parent = parent
            self.created_task = task
            return SimpleNamespace(name=task["name"])

    modules = {
        "google.cloud.tasks_v2": SimpleNamespace(
            CloudTasksClient=FakeClient,
            HttpMethod=SimpleNamespace(POST="POST"),
        ),
        "google.api_core.exceptions": SimpleNamespace(AlreadyExists=FakeAlreadyExists),
        "google.protobuf.timestamp_pb2": SimpleNamespace(Timestamp=FakeTimestamp),
        "google.protobuf.duration_pb2": SimpleNamespace(Duration=FakeDuration),
    }
    monkeypatch.setattr(task_adapter, "load_google_module", lambda name, package: modules[name])
    client = FakeClient()
    queue = CloudTasksQueue(
        project="demo",
        location="us-central1",
        queue="analysis",
        target_base_url="https://service.example",
        shared_secret=TASK_SECRET,
        client=client,
        clock=lambda: datetime(2026, 8, 29, 18, tzinfo=UTC),
    )

    queued = queue.enqueue(
        "internal/tasks/analyses",
        {"analysis_id": "analysis-1"},
        deduplication_key="request-1",
    )

    http_request = client.created_task["http_request"]
    assert http_request["url"] == "https://service.example/internal/tasks/analyses"
    assert http_request["headers"] == {
        "Content-Type": "application/json",
        TASK_SECRET_HEADER: TASK_SECRET,
    }
    assert json.loads(http_request["body"]) == {"analysis_id": "analysis-1"}
    assert TASK_SECRET not in repr(queued)
    assert CLOUD_TASK_NAME_HEADER not in http_request["headers"]
    assert client.created_task["dispatch_deadline"].seconds == 900


def test_cloud_tasks_readiness_requires_https_and_a_strong_secret_in_production(settings):
    settings.IS_PRODUCTION = True
    settings.TASK_BACKEND = "cloud_tasks"
    settings.GOOGLE_CLOUD_PROJECT = "demo"
    settings.CLOUD_TASKS_LOCATION = "us-central1"
    settings.CLOUD_TASKS_QUEUE = "analysis"
    settings.CLOUD_TASKS_BASE_URL = "http://service.example"
    settings.CLOUD_TASKS_SERVICE_ACCOUNT = "tasks@demo-project.iam.gserviceaccount.com"
    settings.CLOUD_TASKS_SHARED_SECRET = TASK_SECRET

    assert backend_configuration()["cloud_tasks"] is False

    settings.CLOUD_TASKS_BASE_URL = "https://service.example"
    settings.CLOUD_TASKS_SHARED_SECRET = "too-short"
    assert backend_configuration()["cloud_tasks"] is False

    settings.CLOUD_TASKS_SHARED_SECRET = TASK_SECRET
    assert backend_configuration()["cloud_tasks"] is True

    settings.CLOUD_TASKS_BASE_URL = "https://service.example/internal/tasks"
    assert backend_configuration()["cloud_tasks"] is False

    settings.CLOUD_TASKS_BASE_URL = "https://service.example?target=other"
    assert backend_configuration()["cloud_tasks"] is False

    settings.CLOUD_TASKS_BASE_URL = "https://service.example"
    settings.CLOUD_TASKS_SERVICE_ACCOUNT = ""
    assert backend_configuration()["cloud_tasks"] is False

    settings.CLOUD_TASKS_SERVICE_ACCOUNT = "not-a-service-account@example.com"
    assert backend_configuration()["cloud_tasks"] is False

    settings.CLOUD_TASKS_SERVICE_ACCOUNT = "tasks@demo-project.iam.gserviceaccount.com"
    settings.CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS = 59
    assert backend_configuration()["cloud_tasks"] is False
