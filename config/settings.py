"""Django settings for the 1415 Agri application."""

import os
from pathlib import Path
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean value.")


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be an integer.") from exc


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_optional_uuid_pair(first_name: str, second_name: str) -> tuple[UUID | None, UUID | None]:
    first_value = os.getenv(first_name, "").strip()
    second_value = os.getenv(second_name, "").strip()
    if bool(first_value) != bool(second_value):
        raise ImproperlyConfigured(f"{first_name} and {second_name} must be set together.")
    if not first_value:
        return None, None
    try:
        return UUID(first_value), UUID(second_value)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"{first_name} and {second_name} must be valid UUID values."
        ) from exc


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"
DEBUG = env_bool("DJANGO_DEBUG", not IS_PRODUCTION)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production.")
    SECRET_KEY = "django-insecure-development-only-never-use-in-production"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver")
if IS_PRODUCTION and not os.getenv("DJANGO_ALLOWED_HOSTS", "").strip():
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS is required in production.")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "agriculture.apps.AgricultureConfig",
    "core.apps.CoreConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "core.context_processors.product",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Agricultural data is persisted through the repository adapters configured
# below (Firestore in production). Django itself does not need a relational
# database because the demonstration session is stored in a signed cookie.
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("pt-br", "Português (Brasil)"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "America/Sao_Paulo")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if IS_PRODUCTION
            else "whitenoise.storage.CompressedStaticFilesStorage"
        ),
    }
}

PRODUCT_NAME = os.getenv("PRODUCT_NAME", "1415 Agri").strip() or "1415 Agri"
DEMO_USERNAME = os.getenv("DEMO_USERNAME", "").strip()
DEMO_PASSWORD_HASH = os.getenv("DEMO_PASSWORD_HASH", "").strip()
PREPARED_DEMO_FIELD_ID, PREPARED_DEMO_ANALYSIS_ID = env_optional_uuid_pair(
    "AA_PREPARED_DEMO_FIELD_ID",
    "AA_PREPARED_DEMO_ANALYSIS_ID",
)

# Public contact form. Messages are never persisted by Django: validated
# requests are verified with Turnstile and handed directly to Resend.
CONTACT_TURNSTILE_ENABLED = env_bool("CONTACT_TURNSTILE_ENABLED", IS_PRODUCTION)
CONTACT_TURNSTILE_SITE_KEY = os.getenv("CONTACT_TURNSTILE_SITE_KEY", "").strip()
CONTACT_TURNSTILE_SECRET_KEY = os.getenv("CONTACT_TURNSTILE_SECRET_KEY", "").strip()
CONTACT_TURNSTILE_HOSTNAMES = env_list(
    "CONTACT_TURNSTILE_HOSTNAMES",
    "1415agri.com,www.1415agri.com",
)
CONTACT_TURNSTILE_ACTION = os.getenv("CONTACT_TURNSTILE_ACTION", "contact").strip() or "contact"
CONTACT_TURNSTILE_TIMEOUT_SECONDS = env_int("CONTACT_TURNSTILE_TIMEOUT_SECONDS", 8)
CONTACT_RESEND_API_KEY = os.getenv("CONTACT_RESEND_API_KEY", "").strip()
CONTACT_RESEND_TIMEOUT_SECONDS = env_int("CONTACT_RESEND_TIMEOUT_SECONDS", 10)
CONTACT_FROM_EMAIL = (
    os.getenv("CONTACT_FROM_EMAIL", "1415 Agri <contato@1415agri.com>").strip()
    or "1415 Agri <contato@1415agri.com>"
)
CONTACT_TO_EMAIL = os.getenv("CONTACT_TO_EMAIL", "").strip()

SESSION_COOKIE_NAME = "agentic_agriculture_session"
SESSION_COOKIE_AGE = env_int("DJANGO_SESSION_COOKIE_AGE", 8 * 60 * 60)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", IS_PRODUCTION)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", IS_PRODUCTION)

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", IS_PRODUCTION)
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 31_536_000 if IS_PRODUCTION else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# PR2 persistence/integration backends. Local development and tests default to
# deterministic in-memory adapters; production must explicitly use Google Cloud.
PERSISTENCE_BACKEND = (
    os.getenv("PERSISTENCE_BACKEND", "firestore" if IS_PRODUCTION else "memory").strip().lower()
)
ARTIFACT_BACKEND = (
    os.getenv("ARTIFACT_BACKEND", "gcs" if IS_PRODUCTION else "memory").strip().lower()
)
TASK_BACKEND = (
    os.getenv("TASK_BACKEND", "cloud_tasks" if IS_PRODUCTION else "memory").strip().lower()
)
BOUNDARY_BACKEND = (
    os.getenv("BOUNDARY_BACKEND", "geospatial" if IS_PRODUCTION else "fixture").strip().lower()
)
ANALYSIS_PIPELINE_BACKEND = (
    os.getenv("ANALYSIS_PIPELINE_BACKEND", "sentinel" if IS_PRODUCTION else "disabled")
    .strip()
    .lower()
)
ANALYSIS_TARGET_SCENE_COUNT = env_int("ANALYSIS_TARGET_SCENE_COUNT", 6)
ANALYSIS_MAX_DIMENSION = env_int("ANALYSIS_MAX_DIMENSION", 512)
ANALYSIS_LEASE_SECONDS = env_int("ANALYSIS_LEASE_SECONDS", 20 * 60)

if IS_PRODUCTION and (
    PERSISTENCE_BACKEND != "firestore"
    or ARTIFACT_BACKEND != "gcs"
    or TASK_BACKEND != "cloud_tasks"
    or BOUNDARY_BACKEND != "geospatial"
    or ANALYSIS_PIPELINE_BACKEND != "sentinel"
):
    raise ImproperlyConfigured(
        "Production requires Firestore, Cloud Storage, Cloud Tasks, geospatial boundaries "
        "and the Sentinel analysis pipeline."
    )

GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
FIRESTORE_DATABASE = os.getenv("FIRESTORE_DATABASE", "(default)").strip() or "(default)"
GCS_BUCKET = os.getenv("GCS_BUCKET", "").strip()
CLOUD_TASKS_LOCATION = os.getenv("CLOUD_TASKS_LOCATION", "").strip()
CLOUD_TASKS_QUEUE = os.getenv("CLOUD_TASKS_QUEUE", "").strip()
CLOUD_TASKS_BASE_URL = os.getenv("CLOUD_TASKS_BASE_URL", "").strip()
CLOUD_TASKS_SERVICE_ACCOUNT = os.getenv("CLOUD_TASKS_SERVICE_ACCOUNT", "").strip()
CLOUD_TASKS_SHARED_SECRET = os.getenv("CLOUD_TASKS_SHARED_SECRET", "")
CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS = env_int("CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS", 900)

# Server-side gateway to the private Google ADK service. The browser only talks
# to Django; Cloud Run identity tokens and the private origin never leave the
# web service. An empty URL keeps local development importable while making
# agent turns fail closed with a versioned 503 response.
AGENT_API_URL = os.getenv("AGENT_API_URL", "").strip().rstrip("/")
AGENT_API_AUDIENCE = os.getenv("AGENT_API_AUDIENCE", "").strip().rstrip("/")
AGENT_API_TIMEOUT_SECONDS = env_int("AGENT_API_TIMEOUT_SECONDS", 90)
AGENT_APP_NAME = os.getenv("AGENT_APP_NAME", "agentic_agriculture").strip()
AGENT_MODEL = os.getenv("AGENT_MODEL", "gemini-3.5-flash").strip()

API_SCHEMA_VERSION = "1.0"
API_MAX_REQUEST_BYTES = env_int("API_MAX_REQUEST_BYTES", 256 * 1024)
ANALYSIS_DAILY_LIMIT = env_int("ANALYSIS_DAILY_LIMIT", 3)
DATA_UPLOAD_MAX_MEMORY_SIZE = API_MAX_REQUEST_BYTES
