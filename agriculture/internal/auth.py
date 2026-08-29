from collections.abc import Callable
from functools import wraps

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from agriculture.api.errors import APIError
from agriculture.internal.security import (
    CLOUD_TASK_NAME_HEADER,
    TASK_SECRET_HEADER,
    task_secret_is_valid,
    task_secrets_match,
)


def internal_task_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Authenticate a Cloud Tasks delivery on an otherwise public service."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        expected = settings.CLOUD_TASKS_SHARED_SECRET
        if not task_secret_is_valid(expected):
            raise APIError(
                code="task_authentication_unavailable",
                message="Internal task authentication is not configured.",
                status=503,
            )

        provided = request.headers.get(TASK_SECRET_HEADER, "")
        if not provided or not task_secrets_match(provided, expected):
            raise APIError(
                code="task_authentication_failed",
                message="Internal task authentication failed.",
                status=401,
            )

        task_name = request.headers.get(CLOUD_TASK_NAME_HEADER, "").strip()
        if not task_name or len(task_name) > 500:
            raise APIError(
                code="invalid_task_delivery",
                message="A valid Cloud Tasks task name header is required.",
                status=400,
            )
        return view(request, *args, cloud_task_name=task_name, **kwargs)

    return wrapped
