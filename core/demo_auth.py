"""Database-free authentication helpers for the demonstration account."""

import secrets
from collections.abc import Callable
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher, is_password_usable
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.crypto import salted_hmac

AUTH_SESSION_KEY = "demo_auth_version"
AUTH_ACTOR_KEY = "demo_actor_id"


def credentials_are_configured() -> bool:
    encoded = settings.DEMO_PASSWORD_HASH
    if not settings.DEMO_USERNAME or not encoded or not is_password_usable(encoded):
        return False
    try:
        identify_hasher(encoded)
    except ValueError:
        return False
    return True


def _credential_version() -> str:
    credential_material = f"{settings.DEMO_USERNAME}\0{settings.DEMO_PASSWORD_HASH}"
    return salted_hmac("core.demo-auth-version", credential_material).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    """Verify both fields while avoiding username-based password timing differences."""
    encoded = settings.DEMO_PASSWORD_HASH
    password_matches = check_password(password, encoded) if encoded else False
    username_matches = secrets.compare_digest(username, settings.DEMO_USERNAME)
    return credentials_are_configured() and username_matches and password_matches


def is_demo_authenticated(request: HttpRequest) -> bool:
    if not credentials_are_configured():
        return False

    stored_version = request.session.get(AUTH_SESSION_KEY)
    actor_id = request.session.get(AUTH_ACTOR_KEY)
    if not isinstance(stored_version, str) or not isinstance(actor_id, str) or not actor_id:
        return False

    authenticated = secrets.compare_digest(stored_version, _credential_version())
    if not authenticated:
        request.session.pop(AUTH_SESSION_KEY, None)
    return authenticated


def begin_demo_session(request: HttpRequest) -> None:
    request.session.cycle_key()
    request.session[AUTH_SESSION_KEY] = _credential_version()
    request.session[AUTH_ACTOR_KEY] = secrets.token_urlsafe(18)


def get_demo_actor_id(request: HttpRequest) -> str | None:
    if not is_demo_authenticated(request):
        return None
    actor_id = request.session.get(AUTH_ACTOR_KEY)
    return actor_id if isinstance(actor_id, str) else None


def end_demo_session(request: HttpRequest) -> None:
    request.session.flush()


def demo_login_required(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not is_demo_authenticated(request):
            login_url = reverse("login")
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{login_url}?{query}")
        return view(request, *args, **kwargs)

    return wrapped
