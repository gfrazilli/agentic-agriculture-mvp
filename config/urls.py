"""Root URL configuration."""

from django.conf.urls.i18n import set_language
from django.urls import include, path

urlpatterns = [
    path("i18n/setlang/", set_language, name="set_language"),
    path("", include("core.urls")),
]
