from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest, HttpResponse

from agriculture.api.errors import APIError
from agriculture.api.responses import error_response
from core.demo_auth import get_demo_actor_id


def api_login_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        actor_id = get_demo_actor_id(request)
        if actor_id is None:
            return error_response(
                APIError(
                    code="authentication_required",
                    message="Authentication is required.",
                    status=401,
                )
            )
        return view(request, *args, actor_id=actor_id, **kwargs)

    return wrapped
