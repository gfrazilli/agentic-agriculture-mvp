from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agriculture.adapters import InMemoryAgricultureRepository
from agriculture.domain import AnalysisStatus
from agriculture.ports import AnalysisLeaseActive, AnalysisLeaseLost
from agriculture.schemas import Analysis, AnalysisError, AnalysisProgress, AnalysisStage

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def _queued_analysis() -> Analysis:
    return Analysis(
        id=uuid4(),
        field_id=uuid4(),
        status=AnalysisStatus.QUEUED,
        progress=AnalysisProgress(
            percent=0,
            stage=AnalysisStage.QUEUED,
            message_pt="Análise enfileirada.",
            message_en="Analysis queued.",
            updated_at=NOW,
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _progress(percent: int, stage: AnalysisStage, at: datetime) -> AnalysisProgress:
    return AnalysisProgress(
        percent=percent,
        stage=stage,
        message_pt=f"Etapa {stage.value}.",
        message_en=f"Stage {stage.value}.",
        updated_at=at,
    )


def _running_update(
    analysis: Analysis, percent: int, stage: AnalysisStage, at: datetime
) -> Analysis:
    return Analysis(
        id=analysis.id,
        field_id=analysis.field_id,
        parent_analysis_id=analysis.parent_analysis_id,
        status=AnalysisStatus.RUNNING,
        requested_zone_count=analysis.requested_zone_count,
        progress=_progress(percent, stage, at),
        created_at=analysis.created_at,
        updated_at=at,
    )


def _failed_update(analysis: Analysis, *, retryable: bool, at: datetime) -> Analysis:
    return Analysis(
        id=analysis.id,
        field_id=analysis.field_id,
        parent_analysis_id=analysis.parent_analysis_id,
        status=AnalysisStatus.FAILED,
        requested_zone_count=analysis.requested_zone_count,
        progress=_progress(analysis.progress.percent, AnalysisStage.FAILED, at),
        error=AnalysisError(
            code="TEST_WORKER_FAILURE",
            message="Safe test failure.",
            retryable=retryable,
            occurred_at=at,
        ),
        created_at=analysis.created_at,
        updated_at=at,
    )


def test_concurrent_claim_has_exactly_one_owner_and_generic_save_is_fenced():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    queued = repository.save_analysis(_queued_analysis())
    initial = _progress(10, AnalysisStage.ACQUIRING_SCENES, NOW)

    def claim(_index: int):
        return repository.claim_analysis_work(
            str(queued.id),
            initial,
            lease_seconds=60,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        claims = list(executor.map(claim, range(20)))

    acquired = [claim for claim in claims if claim.outcome == "acquired"]
    assert len(acquired) == 1
    assert sum(claim.outcome == "busy" for claim in claims) == 19
    assert acquired[0].lease is not None
    assert acquired[0].lease.token not in repr(acquired[0].lease)
    with pytest.raises(AnalysisLeaseActive):
        repository.save_analysis(queued)


def test_checkpoint_uses_token_generation_revision_and_expiry_as_cas():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    queued = repository.save_analysis(_queued_analysis())
    claim = repository.claim_analysis_work(
        str(queued.id),
        _progress(10, AnalysisStage.ACQUIRING_SCENES, NOW),
        lease_seconds=30,
        now=NOW,
    )
    assert claim.lease is not None
    update = _running_update(
        claim.analysis,
        45,
        AnalysisStage.COMPUTING_INDICES,
        NOW + timedelta(seconds=1),
    )

    saved, renewed = repository.checkpoint_analysis_work(
        update,
        claim.lease,
        lease_seconds=30,
        now=NOW + timedelta(seconds=1),
    )

    assert saved.progress.percent == 45
    assert renewed.revision == claim.lease.revision + 1
    with pytest.raises(AnalysisLeaseLost):
        repository.checkpoint_analysis_work(
            update,
            claim.lease,
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(AnalysisLeaseLost):
        repository.checkpoint_analysis_work(
            update,
            replace(renewed, token="wrong-worker-token"),
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(AnalysisLeaseLost):
        repository.checkpoint_analysis_work(
            update,
            renewed,
            now=renewed.expires_at,
        )


def test_stale_takeover_fences_old_owner_and_increments_generation():
    clock = [NOW]
    repository = InMemoryAgricultureRepository(clock=lambda: clock[0])
    queued = repository.save_analysis(_queued_analysis())
    first = repository.claim_analysis_work(
        str(queued.id),
        _progress(10, AnalysisStage.ACQUIRING_SCENES, NOW),
        lease_seconds=10,
        now=NOW,
    )
    assert first.lease is not None

    clock[0] = NOW + timedelta(seconds=10)
    with pytest.raises(AnalysisLeaseActive):
        repository.save_analysis(queued)
    second = repository.claim_analysis_work(
        str(queued.id),
        _progress(10, AnalysisStage.ACQUIRING_SCENES, clock[0]),
        lease_seconds=10,
        now=clock[0],
    )

    assert second.outcome == "acquired"
    assert second.recovered is True
    assert second.lease is not None
    assert second.lease.generation == first.lease.generation + 1
    assert second.lease.attempt_id != first.lease.attempt_id
    with pytest.raises(AnalysisLeaseLost):
        repository.finalize_analysis_work(
            _failed_update(first.analysis, retryable=True, at=clock[0]),
            first.lease,
            now=clock[0],
        )

    terminal = _failed_update(second.analysis, retryable=False, at=clock[0])
    repository.finalize_analysis_work(terminal, second.lease, now=clock[0])
    replay = repository.claim_analysis_work(
        str(queued.id),
        _progress(10, AnalysisStage.ACQUIRING_SCENES, clock[0]),
        now=clock[0],
    )
    assert replay.outcome == "failed"
    assert replay.lease is None


def test_retryable_failure_starts_a_new_generation_and_may_reset_progress():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    queued = repository.save_analysis(_queued_analysis())
    first = repository.claim_analysis_work(
        str(queued.id),
        _progress(10, AnalysisStage.ACQUIRING_SCENES, NOW),
        now=NOW,
    )
    assert first.lease is not None
    advanced, first_lease = repository.checkpoint_analysis_work(
        _running_update(first.analysis, 70, AnalysisStage.CLUSTERING_ZONES, NOW),
        first.lease,
        now=NOW,
    )
    repository.finalize_analysis_work(
        _failed_update(advanced, retryable=True, at=NOW),
        first_lease,
        now=NOW,
    )

    retry = repository.claim_analysis_work(
        str(queued.id),
        _progress(10, AnalysisStage.ACQUIRING_SCENES, NOW),
        now=NOW,
    )

    assert retry.outcome == "acquired"
    assert retry.recovered is True
    assert retry.analysis.progress.percent == 10
    assert retry.lease is not None
    assert retry.lease.generation == first_lease.generation + 1


def test_recent_legacy_running_analysis_gets_grace_before_takeover():
    repository = InMemoryAgricultureRepository(clock=lambda: NOW)
    queued = repository.save_analysis(_queued_analysis())
    running = _running_update(queued, 10, AnalysisStage.ACQUIRING_SCENES, NOW)
    repository.save_analysis(running)

    active = repository.claim_analysis_work(
        str(queued.id),
        _progress(10, AnalysisStage.ACQUIRING_SCENES, NOW),
        lease_seconds=60,
        now=NOW + timedelta(seconds=59),
    )
    recovered = repository.claim_analysis_work(
        str(queued.id),
        _progress(10, AnalysisStage.ACQUIRING_SCENES, NOW + timedelta(seconds=60)),
        lease_seconds=60,
        now=NOW + timedelta(seconds=60),
    )

    assert active.outcome == "busy"
    assert recovered.outcome == "acquired"
    assert recovered.recovered is True
