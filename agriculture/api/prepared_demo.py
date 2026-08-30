"""Read-only access to the operator-selected, cached demonstration result."""

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from agriculture.api.auth import api_login_required
from agriculture.api.errors import APIError
from agriculture.api.responses import api_response, handle_api_errors, require_api_methods
from agriculture.container import get_repository
from agriculture.domain import AnalysisStatus


def _unavailable() -> APIError:
    return APIError(
        code="prepared_demo_unavailable",
        message="The prepared demonstration is unavailable.",
        status=404,
    )


@require_api_methods("GET")
@handle_api_errors
@api_login_required
def prepared_demo(
    request: HttpRequest,  # noqa: ARG001
    *,
    actor_id: str,  # noqa: ARG001
) -> HttpResponse:
    """Return only a complete, internally consistent prepared demonstration."""

    field_id = settings.PREPARED_DEMO_FIELD_ID
    analysis_id = settings.PREPARED_DEMO_ANALYSIS_ID
    if field_id is None or analysis_id is None:
        raise _unavailable()

    repository = get_repository()
    field = repository.get_field(str(field_id))
    analysis = repository.get_analysis(str(analysis_id))
    if (
        field is None
        or analysis is None
        or analysis.field_id != field.id
        or analysis.status is not AnalysisStatus.COMPLETED
        or analysis.result is None
    ):
        raise _unavailable()

    return api_response(
        {
            "prepared": True,
            "field": field.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        }
    )
