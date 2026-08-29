from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import Client, override_settings
from django.urls import reverse

from core.demo_auth import AUTH_ACTOR_KEY, AUTH_SESSION_KEY
from tests.conftest import TEST_PASSWORD, TEST_PASSWORD_HASH, TEST_USERNAME


def test_home_redirects_anonymous_user_to_login(client):
    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next=%2F"


def test_valid_login_opens_protected_home(client):
    response = client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD, "next": reverse("home")},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    home = client.get(reverse("home"))
    assert home.status_code == 200
    assert TEST_USERNAME in home.content.decode()


def test_invalid_login_uses_generic_error_and_does_not_authenticate(client):
    response = client.post(
        reverse("login"),
        {"username": "someone-else", "password": "wrong-password"},
    )

    assert response.status_code == 200
    assert "Credenciais inválidas" in response.content.decode()
    assert client.get(reverse("home")).status_code == 302


def test_login_rejects_external_next_url(client):
    response = client.post(
        reverse("login"),
        {
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "next": "https://attacker.example/steal",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


def test_logout_requires_post_and_clears_session(client):
    client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )

    assert client.get(reverse("logout")).status_code == 405
    response = client.post(reverse("logout"))
    assert response.status_code == 302
    assert response.url == reverse("login")
    assert client.get(reverse("home")).status_code == 302


def test_csrf_is_required_for_login():
    csrf_client = Client(enforce_csrf_checks=True)

    response = csrf_client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )

    assert response.status_code == 403


def test_signed_cookie_contains_only_auth_version_and_opaque_actor(client):
    client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    cookie = client.cookies[settings.SESSION_COOKIE_NAME]
    decoded_session = SessionStore(session_key=cookie.value).load()

    assert set(decoded_session) == {AUTH_ACTOR_KEY, AUTH_SESSION_KEY}
    assert isinstance(decoded_session[AUTH_ACTOR_KEY], str)
    assert len(decoded_session[AUTH_ACTOR_KEY]) >= 16
    serialized_values = repr(decoded_session)
    assert TEST_USERNAME not in serialized_values
    assert TEST_PASSWORD not in serialized_values
    assert TEST_PASSWORD_HASH not in serialized_values


def test_changing_password_hash_invalidates_existing_session(client):
    client.post(
        reverse("login"),
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )

    with override_settings(DEMO_PASSWORD_HASH=make_password("a-new-password")):
        response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))
