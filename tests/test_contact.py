import httpx
import pytest
from django.test import Client, override_settings
from django.urls import reverse

from core.contact import (
    RESEND_EMAILS_URL,
    TURNSTILE_VERIFY_URL,
    ContactDeliveryError,
    ContactMessage,
    ContactService,
    ContactVerificationError,
)
from core.forms import ContactForm


class RecordingClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def response(status_code=200, payload=None):
    request = httpx.Request("POST", "https://provider.invalid")
    return httpx.Response(status_code, json=payload or {}, request=request)


def valid_form_data(**overrides):
    data = {
        "email": "producer@example.com",
        "subject": "Satellite analysis",
        "message": "I would like to analyze one of our fields.",
        "consent": "on",
        "website": "",
        "turnstile_token": "verified-token",
    }
    data.update(overrides)
    return data


def test_contact_form_accepts_valid_content_and_does_not_define_a_model():
    form = ContactForm(valid_form_data())

    assert form.is_valid(), form.errors
    assert not hasattr(form, "save")
    assert form.cleaned_data["subject"] == "Satellite analysis"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"subject": "x"}, "subject"),
        ({"subject": "Hello\nBcc: target@example.com"}, "subject"),
        ({"message": "too short"}, "message"),
        ({"message": "x" * 4_001}, "message"),
        ({"consent": ""}, "consent"),
        ({"website": "https://spam.invalid"}, "website"),
    ],
)
def test_contact_form_rejects_invalid_or_suspicious_content(overrides, field):
    form = ContactForm(valid_form_data(**overrides))

    assert not form.is_valid()
    assert field in form.errors


def test_contact_form_accepts_cloudflare_widget_field_name():
    data = valid_form_data()
    del data["turnstile_token"]
    data["cf-turnstile-response"] = "cloudflare-token"

    form = ContactForm(data)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["turnstile_token"] == "cloudflare-token"


@override_settings(
    CONTACT_TURNSTILE_ENABLED=True,
    CONTACT_TURNSTILE_SECRET_KEY="turnstile-secret",
    CONTACT_TURNSTILE_HOSTNAMES=["1415agri.com"],
    CONTACT_TURNSTILE_ACTION="contact",
    CONTACT_TURNSTILE_TIMEOUT_SECONDS=7,
    CONTACT_RESEND_API_KEY="resend-secret",
    CONTACT_RESEND_TIMEOUT_SECONDS=9,
    CONTACT_FROM_EMAIL="1415 Agri <contato@1415agri.com>",
    CONTACT_TO_EMAIL="recipient@example.com",
)
def test_submit_verifies_turnstile_then_delivers_plain_text_email():
    client = RecordingClient(
        [
            response(
                payload={
                    "success": True,
                    "hostname": "1415agri.com",
                    "action": "contact",
                }
            ),
            response(payload={"id": "1"}),
        ]
    )
    service = ContactService(client)
    message = ContactMessage(
        email="producer@example.com",
        subject="A field question",
        message="Please contact me about this field.",
        turnstile_token="challenge-token",
    )

    service.submit(message, remote_ip="203.0.113.7")

    assert [call[0] for call in client.calls] == [TURNSTILE_VERIFY_URL, RESEND_EMAILS_URL]
    verification = client.calls[0][1]
    assert verification["data"] == {
        "secret": "turnstile-secret",
        "response": "challenge-token",
        "remoteip": "203.0.113.7",
    }
    assert verification["timeout"] == 7
    delivery = client.calls[1][1]
    assert delivery["headers"] == {"Authorization": "Bearer resend-secret"}
    assert delivery["json"] == {
        "from": "1415 Agri <contato@1415agri.com>",
        "to": ["recipient@example.com"],
        "reply_to": "producer@example.com",
        "subject": "[1415 Agri] A field question",
        "text": "Please contact me about this field.",
    }
    assert delivery["timeout"] == 9


@override_settings(
    CONTACT_TURNSTILE_ENABLED=False,
    CONTACT_RESEND_API_KEY="resend-secret",
    CONTACT_TO_EMAIL="recipient@example.com",
)
def test_disabled_turnstile_skips_verification_in_local_development():
    client = RecordingClient([response(payload={"id": "1"})])
    service = ContactService(client)

    service.submit(ContactMessage("a@example.com", "Subject", "A sufficiently long message."))

    assert [call[0] for call in client.calls] == [RESEND_EMAILS_URL]


@pytest.mark.parametrize("token,secret", [("", "secret"), ("token", "")])
@override_settings(CONTACT_TURNSTILE_ENABLED=True)
def test_enabled_turnstile_fails_closed_when_token_or_secret_is_missing(settings, token, secret):
    settings.CONTACT_TURNSTILE_SECRET_KEY = secret
    client = RecordingClient([])

    with pytest.raises(ContactVerificationError, match="verification failed"):
        ContactService(client).verify_turnstile(token)

    assert client.calls == []


@pytest.mark.parametrize(
    "provider_response",
    [response(503), response(payload={"success": False}), response(payload=["unexpected"])],
)
@override_settings(CONTACT_TURNSTILE_ENABLED=True, CONTACT_TURNSTILE_SECRET_KEY="secret")
def test_turnstile_rejects_provider_failures(provider_response):
    client = RecordingClient([provider_response])

    with pytest.raises(ContactVerificationError, match="verification failed"):
        ContactService(client).verify_turnstile("token")


@override_settings(CONTACT_TURNSTILE_ENABLED=True, CONTACT_TURNSTILE_SECRET_KEY="secret")
def test_turnstile_wraps_network_errors_without_leaking_provider_details():
    request = httpx.Request("POST", TURNSTILE_VERIFY_URL)
    client = RecordingClient([httpx.ReadTimeout("sensitive provider error", request=request)])

    with pytest.raises(ContactVerificationError) as error:
        ContactService(client).verify_turnstile("token")

    assert str(error.value) == "Contact verification failed."


@pytest.mark.parametrize(
    "provider_payload",
    [
        {"success": True, "hostname": "untrusted.example", "action": "contact"},
        {"success": True, "hostname": "1415agri.com", "action": "different-action"},
    ],
)
@override_settings(
    CONTACT_TURNSTILE_ENABLED=True,
    CONTACT_TURNSTILE_SECRET_KEY="secret",
    CONTACT_TURNSTILE_HOSTNAMES=["1415agri.com"],
    CONTACT_TURNSTILE_ACTION="contact",
)
def test_turnstile_rejects_unexpected_hostname_or_action(provider_payload):
    client = RecordingClient([response(payload=provider_payload)])

    with pytest.raises(ContactVerificationError, match="verification failed"):
        ContactService(client).verify_turnstile("token")


@override_settings(CONTACT_TURNSTILE_ENABLED=False, CONTACT_RESEND_API_KEY="")
def test_delivery_fails_closed_without_resend_api_key():
    with pytest.raises(ContactDeliveryError, match="unavailable"):
        ContactService(RecordingClient([])).deliver(
            ContactMessage("a@example.com", "Subject", "A sufficiently long message.")
        )


@override_settings(
    CONTACT_TURNSTILE_ENABLED=False,
    CONTACT_RESEND_API_KEY="secret",
    CONTACT_TO_EMAIL="",
)
def test_delivery_fails_closed_without_private_recipient():
    with pytest.raises(ContactDeliveryError, match="unavailable"):
        ContactService(RecordingClient([])).deliver(
            ContactMessage("a@example.com", "Subject", "A sufficiently long message.")
        )


@pytest.mark.parametrize("provider_response", [response(429), response(500)])
@override_settings(CONTACT_RESEND_API_KEY="secret", CONTACT_TO_EMAIL="recipient@example.com")
def test_delivery_rejects_non_success_resend_responses(provider_response):
    with pytest.raises(ContactDeliveryError, match="delivery failed"):
        ContactService(RecordingClient([provider_response])).deliver(
            ContactMessage("a@example.com", "Subject", "A sufficiently long message.")
        )


@override_settings(CONTACT_RESEND_API_KEY="secret", CONTACT_TO_EMAIL="recipient@example.com")
def test_delivery_wraps_network_errors_without_leaking_provider_details():
    request = httpx.Request("POST", RESEND_EMAILS_URL)
    client = RecordingClient([httpx.ReadTimeout("sensitive provider error", request=request)])

    with pytest.raises(ContactDeliveryError) as error:
        ContactService(client).deliver(
            ContactMessage("a@example.com", "Subject", "A sufficiently long message.")
        )

    assert str(error.value) == "Contact delivery failed."


def test_landing_get_exposes_an_empty_contact_form(client):
    result = client.get(reverse("home"))

    assert result.status_code == 200
    assert isinstance(result.context["contact_form"], ContactForm)
    assert not result.context["contact_form"].is_bound
    assert result.context["contact_sent"] is False


def test_contact_get_is_method_not_allowed(client):
    assert client.get(reverse("contact")).status_code == 405


def test_invalid_contact_post_returns_400_and_preserves_bound_form(client, monkeypatch):
    def must_not_submit(*args, **kwargs):
        raise AssertionError("invalid contact form must not reach the provider")

    monkeypatch.setattr("core.views.ContactService.submit", must_not_submit)
    result = client.post(reverse("contact"), valid_form_data(email="invalid"))

    assert result.status_code == 400
    form = result.context["contact_form"]
    assert form.is_bound
    assert form.data["subject"] == "Satellite analysis"
    assert "email" in form.errors
    assert result.context["contact_failed"] is True
    assert result.headers["Cache-Control"] == "no-store, private"
    assert result.headers["Pragma"] == "no-cache"


def test_honeypot_contact_post_fakes_success_without_delivery(client, monkeypatch):
    def must_not_submit(*args, **kwargs):
        raise AssertionError("honeypot submission must not reach the provider")

    monkeypatch.setattr("core.views.ContactService.submit", must_not_submit)
    result = client.post(
        reverse("contact"),
        valid_form_data(website="https://spam.invalid"),
    )

    assert result.status_code == 302
    assert result.url == f"{reverse('home')}?contact=sent#contact-form"


def test_provider_failure_returns_generic_502_with_bound_form(client, monkeypatch):
    def fail_delivery(*args, **kwargs):
        raise ContactDeliveryError("provider internals that must not be rendered")

    monkeypatch.setattr("core.views.ContactService.submit", fail_delivery)
    result = client.post(reverse("contact"), valid_form_data())

    assert result.status_code == 502
    assert result.context["contact_form"].is_bound
    assert result.context["contact_form"].non_field_errors()
    assert b"provider internals" not in result.content
    assert result.headers["Cache-Control"] == "no-store, private"


def test_valid_contact_post_uses_prg_without_collecting_the_visitor_ip(client, monkeypatch):
    recorded = {}

    def record_submission(service, message, *, remote_ip=""):
        recorded["message"] = message
        recorded["remote_ip"] = remote_ip

    monkeypatch.setattr("core.views.ContactService.submit", record_submission)
    result = client.post(
        reverse("contact"),
        valid_form_data(),
        HTTP_CF_CONNECTING_IP="203.0.113.11",
    )

    assert result.status_code == 302
    assert result.url == f"{reverse('home')}?contact=sent#contact-form"
    assert recorded == {
        "message": ContactMessage(
            email="producer@example.com",
            subject="Satellite analysis",
            message="I would like to analyze one of our fields.",
            turnstile_token="verified-token",
        ),
        "remote_ip": "",
    }


def test_contact_post_requires_csrf_token():
    csrf_client = Client(enforce_csrf_checks=True)

    assert csrf_client.post(reverse("contact"), valid_form_data()).status_code == 403
