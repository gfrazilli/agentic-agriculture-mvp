from uuid import UUID

from django.http import HttpRequest, HttpResponse

from agriculture.api.auth import api_login_required
from agriculture.api.errors import APIError
from agriculture.api.models import (
    AgentSessionCreateInput,
    AgentSessionPatchInput,
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
from agriculture.container import get_agriculture_service
from agriculture.fixture_loader import fixture_names, load_fixture
from agriculture.services.idempotency import (
    ServiceResult,
    context_from_request,
)


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
