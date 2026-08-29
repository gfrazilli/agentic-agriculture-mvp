"""Root URL configuration."""

from django.conf.urls.i18n import set_language
from django.urls import include, path

handler404 = "config.errors.page_not_found"

urlpatterns = [
    path("i18n/setlang/", set_language, name="set_language"),
    path("internal/tasks/", include("agriculture.internal.urls")),
    path("api/v1/", include("agriculture.api.urls")),
    path("", include("core.urls")),
]
