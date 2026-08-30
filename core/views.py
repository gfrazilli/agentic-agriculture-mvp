from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from agriculture.checks import backend_configuration
from core.contact import ContactMessage, ContactService, ContactServiceError
from core.demo_auth import (
    begin_demo_session,
    credentials_are_configured,
    demo_login_required,
    end_demo_session,
    is_demo_authenticated,
    verify_credentials,
)
from core.forms import ContactForm, LoginForm


def _safe_next_url(request: HttpRequest, candidate: str | None) -> str:
    fallback = reverse("demo")
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
    return redirect("home")


@require_GET
def landing_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/landing.html",
        _landing_context(
            contact_sent=request.GET.get("contact") == "sent",
        ),
    )


def _landing_context(
    *,
    contact_form: ContactForm | None = None,
    contact_sent: bool = False,
    contact_failed: bool = False,
) -> dict[str, object]:
    return {
        "contact_form": contact_form or ContactForm(),
        "contact_sent": contact_sent,
        "contact_failed": contact_failed,
        "turnstile_enabled": settings.CONTACT_TURNSTILE_ENABLED,
        "turnstile_site_key": settings.CONTACT_TURNSTILE_SITE_KEY,
    }


@require_POST
def contact_view(request: HttpRequest) -> HttpResponse:
    form = ContactForm(request.POST)

    # Silently accept honeypot submissions so automated senders do not learn
    # how to bypass it. Nothing is sent and no submitted content is logged.
    if request.POST.get("website", "").strip():
        return redirect(f"{reverse('home')}?contact=sent#contact")

    if not form.is_valid():
        return _private_no_store(
            render(
                request,
                "core/landing.html",
                _landing_context(contact_form=form, contact_failed=True),
                status=400,
            )
        )

    try:
        ContactService().submit(ContactMessage.from_cleaned_data(form.cleaned_data))
    except ContactServiceError:
        form.add_error(
            None,
            _("We could not send your message right now. Please try again."),
        )
        return _private_no_store(
            render(
                request,
                "core/landing.html",
                _landing_context(contact_form=form, contact_failed=True),
                status=502,
            )
        )

    return redirect(f"{reverse('home')}?contact=sent#contact")


@require_GET
@demo_login_required
def demo_view(request: HttpRequest) -> HttpResponse:
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


def _private_no_store(response: HttpResponse) -> HttpResponse:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return response


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    return _no_store(JsonResponse({"status": "ok"}))


def _contact_configuration_is_ready() -> bool:
    delivery_values = (
        settings.CONTACT_RESEND_API_KEY,
        settings.CONTACT_TO_EMAIL,
    )
    if (
        not settings.IS_PRODUCTION
        and not settings.CONTACT_TURNSTILE_ENABLED
        and not any(delivery_values)
    ):
        return True
    if not all(delivery_values):
        return False
    if not settings.CONTACT_TURNSTILE_ENABLED:
        return True
    return all((settings.CONTACT_TURNSTILE_SITE_KEY, settings.CONTACT_TURNSTILE_SECRET_KEY))


@require_GET
def readyz(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    checks = {
        "demo_credentials": credentials_are_configured(),
        "contact_delivery": _contact_configuration_is_ready(),
        **backend_configuration(),
    }
    ready = all(checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
    }
    return _no_store(JsonResponse(payload, status=200 if ready else 503))
