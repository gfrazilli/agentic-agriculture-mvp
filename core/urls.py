from django.urls import path

from core import views

urlpatterns = [
    path("", views.landing_view, name="home"),
    path("pt/", views.landing_portuguese_view, name="home_pt"),
    path("demo/", views.demo_view, name="demo"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("contact/", views.contact_view, name="contact"),
    # Cloud Run reserves some paths ending in "z". Keep the stable route names
    # for Django callers while exposing portable HTTP paths.
    path("live", views.healthz, name="healthz"),
    path("ready", views.readyz, name="readyz"),
]
