from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from pydantic import BaseModel

from agriculture.api.errors import APIError


def _serialize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def api_response(data: Any, *, status: int = 200) -> JsonResponse:
    response = JsonResponse(
        {"schema_version": settings.API_SCHEMA_VERSION, "data": _serialize(data)},
        status=status,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def error_response(error: APIError) -> JsonResponse:
    payload: dict[str, Any] = {
        "schema_version": settings.API_SCHEMA_VERSION,
        "error": {"code": error.code, "message": error.message},
    }
    if error.details:
        payload["error"]["details"] = error.details
    response = JsonResponse(payload, status=error.status)
    response.headers["Cache-Control"] = "no-store"
    for name, value in error.headers.items():
        response.headers[name] = value
    return response


def handle_api_errors(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        try:
            return view(request, *args, **kwargs)
        except APIError as error:
            return error_response(error)

    return wrapped


def require_api_methods(*allowed_methods: str):
    allowed = tuple(method.upper() for method in allowed_methods)

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            if request.method not in allowed:
                response = error_response(
                    APIError(
                        code="method_not_allowed",
                        message="The HTTP method is not allowed for this endpoint.",
                        status=405,
                    )
                )
                response.headers["Allow"] = ", ".join(allowed)
                return response
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
