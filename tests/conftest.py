import os
import django

# Inicializa o Django antes de importar ou usar componentes dele
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import pytest
from django.contrib.auth.hashers import make_password

TEST_USERNAME = "demo-user"
TEST_PASSWORD = "correct-horse-battery-staple"
TEST_PASSWORD_HASH = make_password(TEST_PASSWORD, hasher="pbkdf2_sha256")


@pytest.fixture(autouse=True)
def demo_credentials(settings):
    settings.DEMO_USERNAME = TEST_USERNAME
    settings.DEMO_PASSWORD_HASH = TEST_PASSWORD_HASH
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
