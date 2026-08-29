from django.http import HttpRequest, HttpResponse
from django.views.defaults import page_not_found as django_page_not_found

from agriculture.api.errors import APIError
from agriculture.api.responses import error_response


def page_not_found(
    request: HttpRequest,
    exception: Exception,
) -> HttpResponse:
    if request.path.startswith("/api/v1/"):
        return error_response(
            APIError(
                code="not_found",
                message="The API endpoint or resource was not found.",
                status=404,
            )
        )
    return django_page_not_found(request, exception)
