from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from agriculture.adapters import InMemoryAgricultureRepository
from agriculture.demo import (
    RealDemoPreparationError,
    RealDemoSpec,
    build_redacted_demo_manifest,
    prepare_real_demo,
)
from agriculture.domain import AnalysisStatus
from agriculture.fixture_loader import load_fixture
from agriculture.management.commands import prepare_real_demo as command_module
from agriculture.schemas import Analysis, AnalysisProgress, AnalysisResult, AnalysisStage

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def _private_payload(**changes):
    payload = {
        "schema_version": "1.0",
        "demo_key": "synthetic-offline-demo",
        "name": "Synthetic offline field",
        "crop": "soybean",
        "season_start": "2025-10-15",
        "season_end": "2026-03-10",
        "estimated_area_ha": 12.4,
        "reference_location": {
            "type": "Point",
            "coordinates": [10.123456, 10.654321],
        },
        "boundary": {
            "type": "Polygon",
            "coordinates": [
                [
                    [10.0, 10.0],
                    [11.0, 10.0],
                    [11.0, 11.0],
                    [10.0, 11.0],
                    [10.0, 10.0],
                ]
            ],
        },
        "requested_zone_count": 4,
    }
    payload.update(changes)
    return payload


def _spec(**changes) -> RealDemoSpec:
    return RealDemoSpec.model_validate_json(json.dumps(_private_payload(**changes)))


class CompletingPipeline:
    def __init__(self, repository: InMemoryAgricultureRepository) -> None:
        self.repository = repository
        self.calls: list[str] = []

    def run(self, analysis_id: str):
        self.calls.append(analysis_id)
        queued = self.repository.get_analysis(analysis_id)
        assert queued is not None
        fixture = load_fixture("analysis-result")
        assert isinstance(fixture, Analysis)
        assert fixture.result is not None
        result_payload = fixture.result.model_dump(mode="json")
        scene_ids = (
            "S2A_00AAA_20250101_0_L2A",
            "S2B_00AAA_20250201_0_L2A",
            "S2C_00AAA_20250301_0_L2A",
        )
        result_payload["mode"] = "live"
        for scene, scene_id in zip(result_payload["scenes"], scene_ids, strict=True):
            scene["scene_id"] = scene_id
        result_payload["provenance"]["scene_ids"] = list(scene_ids)
        for zone in result_payload["zones"]:
            for point, scene_id in zip(zone["trajectory"], scene_ids, strict=True):
                point["scene_id"] = scene_id
        live_result = AnalysisResult.model_validate_json(json.dumps(result_payload))
        completed_at = max(NOW, queued.created_at)
        completed = Analysis(
            id=queued.id,
            field_id=queued.field_id,
            parent_analysis_id=None,
            status=AnalysisStatus.COMPLETED,
            requested_zone_count=queued.requested_zone_count,
            progress=AnalysisProgress(
                percent=100,
                stage=AnalysisStage.COMPLETED,
                message_pt="Demonstração real concluída.",
                message_en="Real demonstration completed.",
                updated_at=completed_at,
            ),
            result=live_result,
            error=None,
            created_at=queued.created_at,
            updated_at=completed_at,
        )
        self.repository.save_analysis(completed)
        return SimpleNamespace(status="completed", error_code=None)


class LeakingPipeline:
    def run(self, analysis_id: str):  # noqa: ARG002
        raise RuntimeError("private-token coordinates=10.123456,10.654321")


def test_preparation_is_deterministic_and_reuses_completed_result() -> None:
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    pipeline = CompletingPipeline(repository)

    first = prepare_real_demo(_spec(), repository, pipeline, clock=lambda: NOW)
    second = prepare_real_demo(_spec(), repository, pipeline, clock=lambda: NOW)

    assert first.field_created is True
    assert first.cached_result_reused is False
    assert second.field_created is False
    assert second.cached_result_reused is True
    assert second.field.id == first.field.id
    assert second.analysis.id == first.analysis.id
    assert pipeline.calls == [str(first.analysis.id)]
    assert len(repository.list_fields()) == 1
    assert len(repository.list_analyses()) == 1


def test_same_demo_key_cannot_silently_replace_private_field() -> None:
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    pipeline = CompletingPipeline(repository)
    prepare_real_demo(_spec(), repository, pipeline, clock=lambda: NOW)

    with pytest.raises(RealDemoPreparationError, match="coordinates were not displayed") as error:
        prepare_real_demo(
            _spec(name="A different private field"),
            repository,
            pipeline,
            clock=lambda: NOW,
        )

    assert error.value.code == "DEMO_KEY_CONFLICT"
    assert "10.123456" not in str(error.value)


def test_reuse_only_never_runs_pipeline_on_cache_miss() -> None:
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)

    with pytest.raises(RealDemoPreparationError) as error:
        prepare_real_demo(
            _spec(),
            repository,
            None,
            reuse_only=True,
            clock=lambda: NOW,
        )

    assert error.value.code == "REAL_DEMO_CACHE_MISS"
    assert repository.list_fields() == []
    assert repository.list_analyses() == []


def test_pipeline_exception_details_are_suppressed() -> None:
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)

    with pytest.raises(RealDemoPreparationError) as error:
        prepare_real_demo(
            _spec(),
            repository,
            LeakingPipeline(),
            clock=lambda: NOW,
        )

    assert error.value.code == "REAL_DEMO_PIPELINE_ERROR"
    assert "RuntimeError" in str(error.value)
    assert "private-token" not in str(error.value)
    assert "10.123456" not in str(error.value)


def test_fixture_result_cannot_be_relabelled_as_a_real_demo_cache() -> None:
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    prepared = prepare_real_demo(
        _spec(),
        repository,
        CompletingPipeline(repository),
        clock=lambda: NOW,
    )
    fixture = load_fixture("analysis-result")
    assert isinstance(fixture, Analysis)
    repository.save_analysis(prepared.analysis.model_copy(update={"result": fixture.result}))

    with pytest.raises(RealDemoPreparationError) as error:
        prepare_real_demo(
            _spec(),
            repository,
            None,
            reuse_only=True,
            clock=lambda: NOW,
        )

    assert error.value.code == "RESULT_NOT_FROM_LIVE_PIPELINE"


def test_redacted_manifest_contains_evidence_but_no_geometry() -> None:
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    prepared = prepare_real_demo(
        _spec(),
        repository,
        CompletingPipeline(repository),
        clock=lambda: NOW,
    )

    manifest = build_redacted_demo_manifest(
        prepared,
        authorization_asserted=True,
        generated_at=NOW,
    )
    encoded = json.dumps(manifest, sort_keys=True)

    assert manifest["authorization"]["operator_asserted_authorized_use"] is True
    assert manifest["analysis"]["status"] == "completed"
    assert manifest["analysis"]["scenes"][0]["scene_id"].startswith("S2")
    assert set(manifest["analysis"]["provenance"]["indices"]) == {
        "NDVI",
        "NDRE",
        "NDMI",
    }
    assert len(manifest["analysis"]["zones"]) == 4
    assert '"coordinates":' not in encoded
    assert "reference_location" not in encoded
    assert '"boundary"' not in encoded
    assert "Synthetic offline field" not in encoded
    assert "synthetic-offline-demo" not in encoded
    assert "10.123456" not in encoded
    assert "10.654321" not in encoded


def test_management_command_requires_explicit_authorization() -> None:
    with pytest.raises(CommandError, match="--confirm-authorized-data"):
        call_command("prepare_real_demo", input="does-not-matter.json")


def test_management_command_runs_offline_and_writes_redacted_manifest(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    # Keep this test independent from pytest's basetemp location. Some CI/local
    # runners place tmp_path inside the repository, while production correctly
    # rejects private inputs there unless they live under .private-demo/.
    settings.BASE_DIR = tmp_path / "application-root"
    input_path = tmp_path / "authorized-field.json"
    input_path.write_text(json.dumps(_private_payload()), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    pipeline = CompletingPipeline(repository)
    monkeypatch.setattr(command_module, "get_repository", lambda: repository)
    monkeypatch.setattr(command_module, "get_analysis_pipeline", lambda: pipeline)
    stdout = StringIO()

    call_command(
        "prepare_real_demo",
        input=str(input_path),
        confirm_authorized_data=True,
        manifest=str(manifest_path),
        stdout=stdout,
    )

    emitted = json.loads(stdout.getvalue())
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert emitted == written
    assert emitted["analysis"]["status"] == "completed"
    assert emitted["redaction"]["coordinates_omitted"] is True
    assert "10.123456" not in stdout.getvalue()


def test_repository_input_must_use_ignored_private_directory(
    tmp_path,
    settings,
) -> None:
    settings.BASE_DIR = tmp_path
    unsafe = tmp_path / "field.json"
    unsafe.write_text("{}", encoding="utf-8")

    with pytest.raises(CommandError, match=r"\.private-demo"):
        command_module.resolve_private_input_path(str(unsafe))

    safe_directory = tmp_path / ".private-demo"
    safe_directory.mkdir()
    safe = safe_directory / "field.json"
    safe.write_text("{}", encoding="utf-8")
    assert command_module.resolve_private_input_path(str(safe)) == safe.resolve()
