"""Analysis lifecycle rules shared by every persistence adapter."""

from __future__ import annotations

from enum import StrEnum


class AnalysisStatus(StrEnum):
    """Stable wire values for the analysis lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class InvalidAnalysisTransition(ValueError):
    """Raised when an analysis lifecycle transition violates the contract."""


class AnalysisStateMachine:
    """Validate status changes and monotonic progress without storage concerns."""

    _ALLOWED: dict[AnalysisStatus, frozenset[AnalysisStatus]] = {
        AnalysisStatus.QUEUED: frozenset(
            {AnalysisStatus.QUEUED, AnalysisStatus.RUNNING, AnalysisStatus.FAILED}
        ),
        AnalysisStatus.RUNNING: frozenset(
            {AnalysisStatus.RUNNING, AnalysisStatus.COMPLETED, AnalysisStatus.FAILED}
        ),
        AnalysisStatus.COMPLETED: frozenset({AnalysisStatus.COMPLETED}),
        AnalysisStatus.FAILED: frozenset({AnalysisStatus.FAILED}),
    }

    @classmethod
    def can_transition(
        cls,
        previous_status: AnalysisStatus,
        previous_progress: int,
        next_status: AnalysisStatus,
        next_progress: int,
    ) -> bool:
        """Return whether a state update is legal.

        Progress may remain unchanged for idempotent updates, but it can never
        decrease. Completed analyses always finish at 100 percent.
        """

        try:
            cls.validate_transition(
                previous_status,
                previous_progress,
                next_status,
                next_progress,
            )
        except InvalidAnalysisTransition:
            return False
        return True

    @classmethod
    def validate_transition(
        cls,
        previous_status: AnalysisStatus,
        previous_progress: int,
        next_status: AnalysisStatus,
        next_progress: int,
    ) -> None:
        """Raise :class:`InvalidAnalysisTransition` for an illegal update."""

        if not 0 <= previous_progress <= 100 or not 0 <= next_progress <= 100:
            raise InvalidAnalysisTransition("Analysis progress must be between 0 and 100.")
        if next_status not in cls._ALLOWED[previous_status]:
            raise InvalidAnalysisTransition(
                f"Analysis cannot transition from {previous_status.value} to {next_status.value}."
            )
        if next_progress < previous_progress:
            raise InvalidAnalysisTransition("Analysis progress cannot decrease.")
        if next_status is AnalysisStatus.COMPLETED and next_progress != 100:
            raise InvalidAnalysisTransition("A completed analysis must have 100% progress.")
