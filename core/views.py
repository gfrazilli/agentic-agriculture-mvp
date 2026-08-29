from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from agriculture.checks import backend_configuration
from core.demo_auth import (
    begin_demo_session,
    credentials_are_configured,
    demo_login_required,
    end_demo_session,
    is_demo_authenticated,
    verify_credentials,
)
from core.forms import LoginForm


def _safe_next_url(request: HttpRequest, candidate: str | None) -> str:
    fallback = reverse("home")
    if not candidate:
        return fallback
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    next_url = _safe_next_url(request, request.POST.get("next") or request.GET.get("next"))
    if is_demo_authenticated(request):
        return redirect(next_url)

    invalid_credentials = False
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid() and verify_credentials(
            form.cleaned_data["username"], form.cleaned_data["password"]
        ):
            begin_demo_session(request)
            return redirect(next_url)
        invalid_credentials = True
    else:
        form = LoginForm()

    return render(
        request,
        "core/login.html",
        {
            "form": form,
            "invalid_credentials": invalid_credentials,
            "credentials_configured": credentials_are_configured(),
            "next": next_url,
        },
    )


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    end_demo_session(request)
    return redirect("login")


@require_GET
@demo_login_required
def home_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/home.html",
        {
            "demo_username": settings.DEMO_USERNAME,
            "guided_demo_default": not settings.IS_PRODUCTION,
        },
    )


def _no_store(response: JsonResponse) -> JsonResponse:
    response.headers["Cache-Control"] = "no-store"
    return response


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    return _no_store(JsonResponse({"status": "ok"}))


@require_GET
def readyz(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    checks = {
        "demo_credentials": credentials_are_configured(),
        **backend_configuration(),
    }
    ready = all(checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
    }
    return _no_store(JsonResponse(payload, status=200 if ready else 503))
