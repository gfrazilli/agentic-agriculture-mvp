from django.test import override_settings
from django.urls import reverse


def test_healthz_is_public_and_cache_disabled(client):
    response = client.get(reverse("healthz"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["Cache-Control"] == "no-store"


def test_readyz_reports_ready_with_valid_demo_credentials(client):
    response = client.get(reverse("readyz"))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"demo_credentials": True},
    }


@override_settings(DEMO_PASSWORD_HASH="not-a-django-hash")
def test_readyz_fails_closed_when_credentials_are_invalid(client):
    response = client.get(reverse("readyz"))

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"demo_credentials": False},
    }


def test_security_headers_are_present(client):
    response = client.get(reverse("healthz"))

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"
