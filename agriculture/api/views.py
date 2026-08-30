import logging
from time import monotonic
from uuid import UUID, uuid4

from django.http import HttpRequest, HttpResponse
from django.utils.html import strip_tags

from agriculture.adapters import (
    AgentAPIConfigurationError,
    AgentAPIProtocolError,
    AgentAPIUnavailableError,
    AgentTurnContext,
)
from agriculture.api.auth import api_login_required
from agriculture.api.errors import APIError
from agriculture.api.models import (
    AgentSessionCreateInput,
    AgentSessionPatchInput,
    AgentTurnCreateInput,
    AnalysisCreateInput,
    EmptyInput,
    FeedbackCreateInput,
    FieldCreateInput,
    FieldPatchInput,
    ReclusterInput,
)
from agriculture.api.parsing import parse_json
from agriculture.api.responses import (
    api_response,
    handle_api_errors,
    require_api_methods,
)
from agriculture.container import get_agent_api_client, get_agriculture_service
from agriculture.fixture_loader import fixture_names, load_fixture
from agriculture.observability import audit_event, get_audit_logger
from agriculture.services.idempotency import (
    ServiceResult,
    context_from_request,
)

logger = get_audit_logger(__name__)


def _result_response(result: ServiceResult) -> HttpResponse:
    response = api_response(result.data, status=result.status)
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@require_api_methods("GET", "POST")
@handle_api_errors
@api_login_required
def fields_collection(request: HttpRequest, *, actor_id: str) -> HttpResponse:
    service = get_agriculture_service()
    if request.method == "GET":
        return api_response(service.list_fields())
    payload = parse_json(request, FieldCreateInput)
    context = context_from_request(request, actor_id)
    return _result_response(service.create_field(payload, context))


@require_api_methods("GET", "PATCH")
@handle_api_errors
@api_login_required
def field_detail(
    request: HttpRequest,
    field_id: UUID,
    *,
    actor_id: str,  # noqa: ARG001
) -> HttpResponse:
    service = get_agriculture_service()
    if request.method == "GET":
        return api_response(service.get_field(field_id))
    payload = parse_json(request, FieldPatchInput)
    return api_response(service.patch_field(field_id, payload))


@require_api_methods("POST")
@handle_api_errors
@api_login_required
def boundary_suggestion(
    request: HttpRequest,
    field_id: UUID,
    *,
    actor_id: str,
) -> HttpResponse:
    parse_json(request, EmptyInput)
    context = context_from_request(request, actor_id)
    result = get_agriculture_service().suggest_boundary(field_id, context)
    return _result_response(result)


@require_api_methods("POST")
@handle_api_errors
@api_login_required
def analyses_collection(request: HttpRequest, *, actor_id: str) -> HttpResponse:
    payload = parse_json(request, AnalysisCreateInput)
    context = context_from_request(request, actor_id)
    result = get_agriculture_service().create_analysis(
        payload,
        context,
        actor_id=actor_id,
    )
    return _result_response(result)


@require_api_methods("GET")
@handle_api_errors
@api_login_required
def analysis_detail(
    request: HttpRequest,  # noqa: ARG001
    analysis_id: UUID,
    *,
    actor_id: str,  # noqa: ARG001
) -> HttpResponse:
    return api_response(get_agriculture_service().get_analysis(analysis_id))


@require_api_methods("POST")
@handle_api_errors
@api_login_required
def analysis_recluster(
    request: HttpRequest,
    analysis_id: UUID,
    *,
    actor_id: str,
) -> HttpResponse:
    payload = parse_json(request, ReclusterInput)
    context = context_from_request(request, actor_id)
    result = get_agriculture_service().recluster_analysis(
        analysis_id,
        payload.zone_count,
        context,
        actor_id=actor_id,
    )
    return _result_response(result)


@require_api_methods("POST")
@handle_api_errors
@api_login_required
def agent_sessions_collection(request: HttpRequest, *, actor_id: str) -> HttpResponse:
    payload = parse_json(request, AgentSessionCreateInput)
    context = context_from_request(request, actor_id)
    result = get_agriculture_service().create_agent_session(payload, context)
    return _result_response(result)


@require_api_methods("GET", "PATCH")
@handle_api_errors
@api_login_required
def agent_session_detail(
    request: HttpRequest,
    session_id: UUID,
    *,
    actor_id: str,  # noqa: ARG001
) -> HttpResponse:
    service = get_agriculture_service()
    if request.method == "GET":
        return api_response(service.get_agent_session(session_id))
    payload = parse_json(request, AgentSessionPatchInput)
    return api_response(service.patch_agent_session(session_id, payload))


@require_api_methods("POST")
@handle_api_errors
@api_login_required
def agent_session_turns(
    request: HttpRequest,
    session_id: UUID,
    *,
    actor_id: str,
) -> HttpResponse:
    payload = parse_json(request, AgentTurnCreateInput)
    service = get_agriculture_service()
    session, field, analysis = service.get_active_agent_session(session_id)
    execution_id = str(uuid4())
    context = AgentTurnContext(
        execution_id=execution_id,
        session_id=str(session.id),
        actor_id=actor_id,
        language=session.language,
        channel=session.channel.value,
        field_id=str(field.id) if field is not None else None,
        analysis_id=str(analysis.id) if analysis is not None else None,
    )
    started_at = monotonic()
    audit_event(
        logger,
        "agent_gateway.turn.started",
        component="web",
        execution_id=execution_id,
        session_id=str(session.id),
        turn_number=session.turn_count + 1,
        field_id=context.field_id,
        analysis_id=context.analysis_id,
        language=context.language,
        channel=context.channel,
        status="started",
    )

    try:
        reply = get_agent_api_client().run_turn(payload.message, context)
    except AgentAPIConfigurationError as exc:
        _log_agent_gateway_failure(
            execution_id=execution_id,
            context=context,
            turn_number=session.turn_count + 1,
            started_at=started_at,
            error_code="agent_unavailable",
            exception=exc,
        )
        raise APIError(
            "agent_unavailable",
            "The agricultural assistant is temporarily unavailable.",
            503,
            headers={"Retry-After": "15"},
        ) from None
    except AgentAPIProtocolError as exc:
        _log_agent_gateway_failure(
            execution_id=execution_id,
            context=context,
            turn_number=session.turn_count + 1,
            started_at=started_at,
            error_code="agent_invalid_response",
            exception=exc,
        )
        raise APIError(
            "agent_invalid_response",
            "The agricultural assistant returned an invalid response.",
            502,
        ) from None
    except AgentAPIUnavailableError as exc:
        _log_agent_gateway_failure(
            execution_id=execution_id,
            context=context,
            turn_number=session.turn_count + 1,
            started_at=started_at,
            error_code="agent_unavailable",
            exception=exc,
        )
        raise APIError(
            "agent_unavailable",
            "The agricultural assistant is temporarily unavailable.",
            503,
            headers={"Retry-After": "15"},
        ) from None

    updated_session = service.patch_agent_session(
        session.id,
        AgentSessionPatchInput(increment_turn_count=True),
    )
    audit_event(
        logger,
        "agent_gateway.turn.completed",
        component="web",
        execution_id=execution_id,
        session_id=str(session.id),
        turn_number=updated_session.turn_count,
        field_id=context.field_id,
        analysis_id=context.analysis_id,
        language=context.language,
        channel=context.channel,
        status="completed",
        duration_ms=max(0, round((monotonic() - started_at) * 1_000)),
        model=reply.model,
        agents=reply.agents,
        tools=reply.tools,
    )
    return api_response(
        {
            "message": {
                "role": "assistant",
                "text": strip_tags(reply.text),
                "format": "plain_text",
            },
            "session": updated_session.model_dump(mode="json"),
            "trace": {
                "provider": "Google Vertex AI",
                "framework": "Google ADK",
                "model": reply.model,
                "agents": list(reply.agents),
                "tools": list(reply.tools),
            },
        }
    )


def _log_agent_gateway_failure(
    *,
    execution_id: str,
    context: AgentTurnContext,
    turn_number: int,
    started_at: float,
    error_code: str,
    exception: Exception,
) -> None:
    """Record a safe failure code and type without formatting the exception."""

    audit_event(
        logger,
        "agent_gateway.turn.failed",
        level=logging.WARNING,
        component="web",
        execution_id=execution_id,
        session_id=context.session_id,
        turn_number=turn_number,
        field_id=context.field_id,
        analysis_id=context.analysis_id,
        language=context.language,
        channel=context.channel,
        status="failed",
        duration_ms=max(0, round((monotonic() - started_at) * 1_000)),
        error_code=error_code,
        error_type=type(exception).__name__,
    )


@require_api_methods("POST")
@handle_api_errors
@api_login_required
def feedback_collection(request: HttpRequest, *, actor_id: str) -> HttpResponse:
    payload = parse_json(request, FeedbackCreateInput)
    context = context_from_request(request, actor_id)
    result = get_agriculture_service().create_feedback(payload, context)
    return _result_response(result)


@require_api_methods("GET")
@handle_api_errors
@api_login_required
def fixtures_index(
    request: HttpRequest,  # noqa: ARG001
    *,
    actor_id: str,  # noqa: ARG001
) -> HttpResponse:
    return api_response({"fixtures": fixture_names()})


@require_api_methods("GET")
@handle_api_errors
@api_login_required
def fixture_detail(
    request: HttpRequest,  # noqa: ARG001
    fixture_name: str,
    *,
    actor_id: str,  # noqa: ARG001
) -> HttpResponse:
    try:
        fixture = load_fixture(fixture_name)
    except KeyError:
        raise APIError("fixture_not_found", "The fixture was not found.", 404) from None
    return api_response(fixture)
