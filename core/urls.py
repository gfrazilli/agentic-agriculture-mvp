from django.urls import path

from core import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Cloud Run reserves some paths ending in "z". Keep the stable route names
    # for Django callers while exposing portable HTTP paths.
    path("live", views.healthz, name="healthz"),
    path("ready", views.readyz, name="readyz"),
]
