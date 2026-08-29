from django.conf import settings


def test_sessions_are_signed_cookies_and_database_is_optional():
    assert settings.SESSION_ENGINE == "django.contrib.sessions.backends.signed_cookies"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.dummy"
