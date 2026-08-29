import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from agriculture.adapters import (
    CloudTasksQueue,
    FirestoreRepository,
    GCSArtifactStore,
    InMemoryAgricultureRepository,
    InMemoryArtifactStore,
    InMemoryTaskQueue,
    MissingGoogleDependency,
)
from agriculture.ports import DailyUsageLimitExceeded, IdempotencyConflict
from agriculture.schemas import AgentSession, Analysis, Feedback, Field

FIXTURES = Path(__file__).parents[1] / "agriculture" / "fixtures"


def _load_contract(filename: str, model_type):
    return model_type.model_validate_json((FIXTURES / filename).read_text(encoding="utf-8"))


def _agent_session() -> AgentSession:
    now = datetime(2026, 8, 29, 12, tzinfo=UTC)
    return AgentSession.model_validate_json(
        json.dumps(
            {
                "id": str(uuid4()),
                "language": "pt-BR",
                "channel": "voice",
                "status": "active",
                "turn_count": 0,
                "started_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=8)).isoformat(),
            }
        )
    )


def _feedback(analysis: Analysis, session: AgentSession) -> Feedback:
    return Feedback.model_validate_json(
        json.dumps(
            {
                "id": str(uuid4()),
                "analysis_id": str(analysis.id),
                "session_id": str(session.id),
                "rating": "helpful",
                "comment": "As zonas ficaram claras.",
                "created_at": "2026-08-29T12:05:00Z",
            }
        )
    )


def test_in_memory_repository_round_trips_all_entities_without_aliasing():
    repository = InMemoryAgricultureRepository()
    field = _load_contract("field-draft.example.json", Field)
    analysis = _load_contract("analysis-running.example.json", Analysis)
    session = _agent_session()
    feedback = _feedback(analysis, session)

    returned_field = repository.save_field(field)
    repository.save_analysis(analysis)
    repository.save_agent_session(session)
    repository.save_feedback(feedback)
    returned_field.name = "Mutated outside the repository"

    assert repository.get_field(str(field.id)) == field
    assert repository.list_fields() == [field]
    assert repository.get_analysis(str(analysis.id)) == analysis
    assert repository.list_analyses(str(field.id)) == [analysis]
    assert repository.get_agent_session(str(session.id)) == session
    assert repository.list_agent_sessions() == [session]
    assert repository.get_feedback(str(feedback.id)) == feedback
    assert repository.list_feedback(str(analysis.id)) == [feedback]


def test_claim_request_is_atomic_idempotent_and_consumes_quota_once():
    now = datetime(2026, 8, 29, 15, tzinfo=UTC)
    repository = InMemoryAgricultureRepository(clock=lambda: now)
    usage_date = date(2026, 8, 29)

    first = repository.claim_request("request-1", "digest-a", "demo", usage_date, 2)
    replay = repository.claim_request("request-1", "digest-a", "demo", usage_date, 2)

    assert first.is_replay is False
    assert first.record.pending is True
    assert replay.is_replay is True
    assert replay.usage_count == 1
    assert repository.get_daily_usage("demo", usage_date) == 1

    with pytest.raises(IdempotencyConflict):
        repository.claim_request("request-1", "different-digest", "demo", usage_date, 2)

    completed = repository.complete_request(
        "request-1", {"analysis_id": "analysis-1"}, request_digest="digest-a"
    )
    assert completed.pending is False
    assert completed.response == {"analysis_id": "analysis-1"}
    assert repository.claim_request(
        "request-1", "digest-a", "demo", usage_date, 2
    ).record.response == {"analysis_id": "analysis-1"}

    repository.claim_request("request-2", "digest-b", "demo", usage_date, 2)
    with pytest.raises(DailyUsageLimitExceeded):
        repository.claim_request("request-3", "digest-c", "demo", usage_date, 2)


def test_generic_idempotency_claim_has_exactly_one_concurrent_owner():
    repository = InMemoryAgricultureRepository()

    def claim(_index: int):
        return repository.claim_idempotency("create-field", "digest-a")

    with ThreadPoolExecutor(max_workers=10) as executor:
        claims = list(executor.map(claim, range(10)))

    assert sum(not claim.is_replay for claim in claims) == 1
    assert all(claim.record.pending for claim in claims)

    repository.complete_request(
        "create-field",
        {"status": 201, "data": {"id": "field-1"}},
        request_digest="digest-a",
    )
    replay = repository.claim_idempotency("create-field", "digest-a")
    assert replay.is_replay is True
    assert replay.record.response == {"status": 201, "data": {"id": "field-1"}}

    with pytest.raises(IdempotencyConflict):
        repository.claim_idempotency("create-field", "digest-b")


def test_idempotency_records_expire_after_24_hours_and_cannot_be_completed_late():
    now = datetime(2026, 8, 29, 15, tzinfo=UTC)
    repository = InMemoryAgricultureRepository(clock=lambda: now)
    repository.claim_request("request-1", "digest-a", "demo", now.date(), 3, now=now)

    expiry = now + timedelta(hours=24)
    assert repository.get_idempotency("request-1", now=expiry) is None
    with pytest.raises(KeyError):
        repository.complete_request(
            "request-1", {"ok": True}, request_digest="digest-a", now=expiry
        )


def test_firestore_expired_idempotency_read_never_deletes_outside_transaction():
    now = datetime(2026, 8, 29, 15, tzinfo=UTC)

    class Snapshot:
        exists = True

        @staticmethod
        def to_dict():
            return {
                "key": "request-1",
                "request_digest": "digest-a",
                "expires_at": now - timedelta(seconds=1),
                "response": None,
                "pending": True,
            }

    class DocumentReference:
        @staticmethod
        def get():
            return Snapshot()

        @staticmethod
        def delete():
            raise AssertionError("expired reads must not delete a potentially replaced record")

    class CollectionReference:
        @staticmethod
        def document(_document_id: str):
            return DocumentReference()

    class Client:
        @staticmethod
        def collection(_name: str):
            return CollectionReference()

    repository = FirestoreRepository(client=Client())

    assert repository.get_idempotency("request-1", now=now) is None


def test_put_idempotency_detects_payload_conflicts_and_returns_defensive_copies():
    repository = InMemoryAgricultureRepository()
    response = {"field": {"id": "field-1"}}
    stored = repository.put_idempotency("create-field", response, request_digest="digest-a")
    response["field"]["id"] = "changed"

    assert stored.pending is False
    assert repository.get_idempotency("create-field").response == {"field": {"id": "field-1"}}
    with pytest.raises(IdempotencyConflict):
        repository.put_idempotency("create-field", {"field": {}}, request_digest="digest-b")


def test_daily_usage_reservation_is_thread_safe():
    repository = InMemoryAgricultureRepository()
    usage_date = date(2026, 8, 29)

    def reserve(index: int) -> bool:
        try:
            repository.claim_request(f"request-{index}", f"digest-{index}", "demo", usage_date, 3)
        except DailyUsageLimitExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=10) as executor:
        outcomes = list(executor.map(reserve, range(10)))

    assert sum(outcomes) == 3
    assert repository.get_daily_usage("demo", usage_date) == 3


def test_in_memory_artifact_store_round_trip_delete_and_key_validation():
    store = InMemoryArtifactStore()
    artifact = store.put_bytes("analysis/a/map.png", b"png-data", content_type="image/png")

    assert artifact.uri == "memory:///analysis/a/map.png"
    assert artifact.size == 8
    assert store.exists(artifact.key)
    assert store.get_bytes(artifact.key) == b"png-data"

    store.delete(artifact.key)
    assert not store.exists(artifact.key)
    with pytest.raises(KeyError):
        store.get_bytes(artifact.key)
    with pytest.raises(ValueError):
        store.put_bytes("../secret", b"no", content_type="text/plain")


def test_in_memory_task_queue_deduplicates_and_copies_payloads():
    now = datetime(2026, 8, 29, 15, tzinfo=UTC)
    queue = InMemoryTaskQueue(clock=lambda: now)
    payload = {"analysis_id": "analysis-1", "nested": {"attempt": 1}}

    first = queue.enqueue("tasks/analyse", payload, deduplication_key="analysis-1")
    payload["nested"]["attempt"] = 2
    replay = queue.enqueue("tasks/analyse", {"ignored": True}, deduplication_key="analysis-1")

    assert replay == first
    assert first.payload["nested"] == {"attempt": 1}
    assert queue.tasks == [first]


def test_google_modules_are_lazy_and_missing_dependencies_fail_on_instantiation(monkeypatch):
    import agriculture.adapters.optional as optional

    real_import = optional.importlib.import_module

    def import_without_google(name: str):
        if name.startswith("google."):
            raise ImportError(name)
        return real_import(name)

    monkeypatch.setattr(optional.importlib, "import_module", import_without_google)

    with pytest.raises(MissingGoogleDependency, match="google-cloud-firestore"):
        FirestoreRepository()
    with pytest.raises(MissingGoogleDependency, match="google-cloud-storage"):
        GCSArtifactStore("artifacts")
    with pytest.raises(MissingGoogleDependency, match="google-cloud-tasks"):
        CloudTasksQueue(
            project="project",
            location="us-central1",
            queue="analysis",
            target_base_url="https://service.example",
        )
