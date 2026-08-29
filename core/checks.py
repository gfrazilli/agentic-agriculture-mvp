from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from core.demo_auth import credentials_are_configured


@register(Tags.security)
def check_demo_credentials(app_configs, **kwargs):  # noqa: ARG001
    if credentials_are_configured():
        return []

    message = "DEMO_USERNAME and a valid Django DEMO_PASSWORD_HASH must be configured."
    hint = "Generate the hash with the command documented in README.md."
    if settings.IS_PRODUCTION:
        return [Error(message, hint=hint, id="core.E001")]
    return [Warning(message, hint=hint, id="core.W001")]
