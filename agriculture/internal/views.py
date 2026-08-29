from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from agriculture.api.errors import APIError
from agriculture.api.parsing import parse_json
from agriculture.api.responses import api_response, handle_api_errors, require_api_methods
from agriculture.container import get_repository
from agriculture.internal.auth import internal_task_required
from agriculture.internal.models import AnalysisTaskInput


@csrf_exempt
@require_api_methods("POST")
@handle_api_errors
@internal_task_required
def receive_analysis_task(
    request: HttpRequest,
    *,
    cloud_task_name: str,  # noqa: ARG001
) -> HttpResponse:
    """Validate and acknowledge analysis work without pretending to run the pipeline.

    A valid delivery is deliberately a read-only operation until the Sentinel/Gemini
    worker exists. Returning 2xx prevents Cloud Tasks from retrying a permanent,
    intentionally unimplemented operation. Because no state changes, duplicate
    deliveries are naturally idempotent.
    """

    payload = parse_json(request, AnalysisTaskInput)
    analysis = get_repository().get_analysis(str(payload.analysis_id))
    if analysis is None:
        raise APIError(
            code="task_analysis_not_found",
            message="The task references an analysis that does not exist.",
            status=404,
        )
    if analysis.field_id != payload.field_id:
        raise APIError(
            code="task_field_mismatch",
            message="The task field does not match the stored analysis.",
            status=409,
        )
    if analysis.parent_analysis_id != payload.parent_analysis_id:
        raise APIError(
            code="task_parent_analysis_mismatch",
            message="The task parent does not match the stored analysis.",
            status=409,
        )

    return api_response(
        {
            "analysis_id": str(analysis.id),
            "outcome": "acknowledged_not_processed",
            "pipeline_implemented": False,
            "reason": "sentinel_gemini_pipeline_not_implemented",
        }
    )
