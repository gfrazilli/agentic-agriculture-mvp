from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from pydantic import ValidationError

from agriculture.api.errors import APIError
from agriculture.api.models import (
    AgentSessionCreateInput,
    AgentSessionPatchInput,
    AnalysisCreateInput,
    FeedbackCreateInput,
    FieldCreateInput,
    FieldPatchInput,
)
from agriculture.domain import AnalysisStatus
from agriculture.fixture_loader import load_fixture
from agriculture.ports.boundaries import BoundaryProvider
from agriculture.ports.repositories import AgricultureRepository
from agriculture.ports.tasks import TaskQueue
from agriculture.schemas import (
    AgentSession,
    AgentSessionStatus,
    Analysis,
    AnalysisProgress,
    AnalysisStage,
    BoundarySuggestion,
    Feedback,
    Field,
    GeoJSONPolygon,
)
from agriculture.services.idempotency import (
    IdempotencyContext,
    ServiceResult,
    claim_idempotent_request,
    claim_limited_request,
    complete_idempotent_request,
    replay_if_present,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _data(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model


class AgricultureService:
    def __init__(
        self,
        repository: AgricultureRepository,
        task_queue: TaskQueue,
        *,
        boundary_provider: BoundaryProvider | None = None,
        clock=utc_now,
    ) -> None:
        self.repository = repository
        self.task_queue = task_queue
        self.boundary_provider = boundary_provider
        self.clock = clock

    def create_field(
        self,
        payload: FieldCreateInput,
        context: IdempotencyContext,
    ) -> ServiceResult:
        if replay := replay_if_present(self.repository, context):
            return replay
        now = self.clock()
        if replay := claim_idempotent_request(
            self.repository,
            context,
            now=now,
        ):
            return replay
        field = Field(
            id=uuid4(),
            name=payload.name,
            crop=payload.crop,
            season_start=payload.season_start,
            season_end=payload.season_end,
            estimated_area_ha=payload.estimated_area_ha,
            reference_location=payload.reference_location,
            boundary=None,
            boundary_confirmed=False,
            created_at=now,
            updated_at=now,
        )
        saved = self.repository.save_field(field)
        result = ServiceResult(data=_data(saved), status=201)
        complete_idempotent_request(self.repository, context, result, now=now)
        return result

    def list_fields(self) -> list[Field]:
        return self.repository.list_fields()

    def get_field(self, field_id: UUID | str) -> Field:
        field = self.repository.get_field(str(field_id))
        if field is None:
            raise APIError("field_not_found", "The field was not found.", 404)
        return field

    def patch_field(self, field_id: UUID | str, payload: FieldPatchInput) -> Field:
        field = self.get_field(field_id)
        changes = payload.model_dump(exclude_unset=True)
        changes["updated_at"] = self.clock()
        try:
            updated = Field.model_validate({**field.model_dump(), **changes})
        except ValidationError as exc:
            raise APIError(
                "validation_error",
                "The requested field update is invalid.",
                422,
                exc.errors(include_url=False, include_context=False),
            ) from None
        return self.repository.save_field(updated)

    def suggest_boundary(
        self,
        field_id: UUID | str,
        context: IdempotencyContext,
    ) -> ServiceResult:
        if replay := replay_if_present(self.repository, context):
            return replay
        field = self.get_field(field_id)
        if self.boundary_provider is None:
            template = load_fixture("boundary-suggestion")
            if not isinstance(template, BoundarySuggestion):
                raise RuntimeError("boundary-suggestion fixture has the wrong contract")

            ring = template.boundary.coordinates[0]
            unique = ring[:-1]
            center_lon = sum(position[0] for position in unique) / len(unique)
            center_lat = sum(position[1] for position in unique) / len(unique)
            target_lon, target_lat = field.reference_location.coordinates
            shifted = tuple(
                (lon - center_lon + target_lon, lat - center_lat + target_lat) for lon, lat in ring
            )
            try:
                boundary = GeoJSONPolygon(coordinates=(shifted,))
            except ValidationError as exc:
                raise APIError(
                    "boundary_suggestion_unavailable",
                    "A safe boundary suggestion could not be generated for this location.",
                    422,
                    exc.errors(include_url=False, include_context=False),
                ) from None
            estimated_area_ha = field.estimated_area_ha
            confidence = template.confidence
            source = template.source
        else:
            proposal = self.boundary_provider.suggest(field)
            boundary = proposal.boundary
            estimated_area_ha = proposal.estimated_area_ha
            confidence = proposal.confidence
            source = proposal.source

        now = self.clock()
        if replay := claim_idempotent_request(
            self.repository,
            context,
            now=now,
        ):
            return replay
        suggestion = BoundarySuggestion(
            id=uuid4(),
            field_id=field.id,
            boundary=boundary,
            estimated_area_ha=estimated_area_ha,
            confidence=confidence,
            source=source,
            requires_confirmation=True,
            generated_at=now,
        )
        result = ServiceResult(data=_data(suggestion), status=201)
        complete_idempotent_request(self.repository, context, result, now=now)
        return result

    def create_analysis(
        self,
        payload: AnalysisCreateInput,
        context: IdempotencyContext,
        *,
        actor_id: str,
    ) -> ServiceResult:
        if replay := replay_if_present(self.repository, context):
            return replay
        field = self.get_field(payload.field_id)
        if not field.boundary_confirmed:
            raise APIError(
                "field_boundary_not_confirmed",
                "Confirm the field boundary before requesting an analysis.",
                409,
            )
        return self._create_queued_analysis(
            field=field,
            requested_zone_count=payload.requested_zone_count,
            parent_analysis_id=None,
            context=context,
            actor_id=actor_id,
        )

    def get_analysis(self, analysis_id: UUID | str) -> Analysis:
        analysis = self.repository.get_analysis(str(analysis_id))
        if analysis is None:
            raise APIError("analysis_not_found", "The analysis was not found.", 404)
        return analysis

    def recluster_analysis(
        self,
        analysis_id: UUID | str,
        zone_count: int,
        context: IdempotencyContext,
        *,
        actor_id: str,
    ) -> ServiceResult:
        if replay := replay_if_present(self.repository, context):
            return replay
        source = self.get_analysis(analysis_id)
        if source.status.value != "completed" or source.result is None:
            raise APIError(
                "analysis_not_completed",
                "Only a completed analysis can be regrouped.",
                409,
            )
        field = self.get_field(source.field_id)
        return self._create_queued_analysis(
            field=field,
            requested_zone_count=zone_count,
            parent_analysis_id=source.id,
            context=context,
            actor_id=actor_id,
        )

    def _create_queued_analysis(
        self,
        *,
        field: Field,
        requested_zone_count: int | None,
        parent_analysis_id: UUID | None,
        context: IdempotencyContext,
        actor_id: str,
    ) -> ServiceResult:
        now = self.clock()
        if replay := claim_limited_request(
            self.repository,
            context,
            subject=actor_id,
            daily_limit=settings.ANALYSIS_DAILY_LIMIT,
            now=now,
        ):
            return replay

        analysis = Analysis(
            id=uuid4(),
            field_id=field.id,
            parent_analysis_id=parent_analysis_id,
            status=AnalysisStatus.QUEUED,
            requested_zone_count=requested_zone_count,
            progress=AnalysisProgress(
                percent=0,
                stage=AnalysisStage.QUEUED,
                message_pt="Análise adicionada à fila.",
                message_en="Analysis added to the queue.",
                updated_at=now,
            ),
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        saved = self.repository.save_analysis(analysis)
        self.task_queue.enqueue(
            "internal/tasks/analyses",
            {
                "analysis_id": str(saved.id),
                "field_id": str(saved.field_id),
                "parent_analysis_id": (
                    str(saved.parent_analysis_id) if saved.parent_analysis_id else None
                ),
            },
            deduplication_key=context.scoped_key,
        )
        result = ServiceResult(data=_data(saved), status=202)
        complete_idempotent_request(self.repository, context, result, now=now)
        return result

    def create_agent_session(
        self,
        payload: AgentSessionCreateInput,
        context: IdempotencyContext,
    ) -> ServiceResult:
        if replay := replay_if_present(self.repository, context):
            return replay
        field = None
        analysis = None
        if payload.field_id is not None:
            field = self.get_field(payload.field_id)
        if payload.analysis_id is not None:
            analysis = self.get_analysis(payload.analysis_id)
        if field is not None and analysis is not None and analysis.field_id != field.id:
            raise APIError(
                "agent_session_context_mismatch",
                "The analysis belongs to a different field.",
                409,
            )
        now = self.clock()
        if replay := claim_idempotent_request(
            self.repository,
            context,
            now=now,
        ):
            return replay
        session = AgentSession(
            id=uuid4(),
            language=payload.language,
            channel=payload.channel,
            status=AgentSessionStatus.ACTIVE,
            field_id=payload.field_id,
            analysis_id=payload.analysis_id,
            turn_count=0,
            started_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        saved = self.repository.save_agent_session(session)
        result = ServiceResult(data=_data(saved), status=201)
        complete_idempotent_request(self.repository, context, result, now=now)
        return result

    def get_agent_session(self, session_id: UUID | str) -> AgentSession:
        session = self.repository.get_agent_session(str(session_id))
        if session is None:
            raise APIError("agent_session_not_found", "The agent session was not found.", 404)
        return session

    def patch_agent_session(
        self,
        session_id: UUID | str,
        payload: AgentSessionPatchInput,
    ) -> AgentSession:
        session = self.get_agent_session(session_id)
        changes = payload.model_dump(exclude_unset=True)
        increment = bool(changes.pop("increment_turn_count", False))
        field_id = changes.get("field_id", session.field_id)
        analysis_id = changes.get("analysis_id", session.analysis_id)
        field = self.get_field(field_id) if field_id is not None else None
        analysis = self.get_analysis(analysis_id) if analysis_id is not None else None
        if field is not None and analysis is not None and analysis.field_id != field.id:
            raise APIError(
                "agent_session_context_mismatch",
                "The analysis belongs to a different field.",
                409,
            )
        if increment:
            changes["turn_count"] = session.turn_count + 1
        changes["updated_at"] = self.clock()
        try:
            updated = AgentSession.model_validate({**session.model_dump(), **changes})
        except ValidationError as exc:
            raise APIError(
                "validation_error",
                "The requested agent-session update is invalid.",
                422,
                exc.errors(include_url=False, include_context=False),
            ) from None
        return self.repository.save_agent_session(updated)

    def create_feedback(
        self,
        payload: FeedbackCreateInput,
        context: IdempotencyContext,
    ) -> ServiceResult:
        if replay := replay_if_present(self.repository, context):
            return replay
        analysis = self.get_analysis(payload.analysis_id)
        session = self.get_agent_session(payload.session_id)
        if session.analysis_id is not None and session.analysis_id != analysis.id:
            raise APIError(
                "feedback_session_mismatch",
                "The agent session is linked to a different analysis.",
                409,
            )
        if payload.zone_id and analysis.result is None:
            raise APIError(
                "analysis_result_unavailable",
                "Zone feedback requires a completed analysis result.",
                409,
            )
        if payload.zone_id and analysis.result is not None:
            known_zones = {zone.zone_id for zone in analysis.result.zones}
            if payload.zone_id not in known_zones:
                raise APIError("zone_not_found", "The analysis zone was not found.", 422)
        now = self.clock()
        if replay := claim_idempotent_request(
            self.repository,
            context,
            now=now,
        ):
            return replay
        feedback = Feedback(
            id=uuid4(),
            analysis_id=analysis.id,
            session_id=session.id,
            rating=payload.rating,
            comment=payload.comment,
            zone_id=payload.zone_id,
            created_at=now,
        )
        saved = self.repository.save_feedback(feedback)
        result = ServiceResult(data=_data(saved), status=201)
        complete_idempotent_request(self.repository, context, result, now=now)
        return result
