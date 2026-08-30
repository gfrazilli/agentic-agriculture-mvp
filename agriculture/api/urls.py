from django.urls import path

from agriculture.api import views

app_name = "agriculture_api"

urlpatterns = [
    path("fields/", views.fields_collection, name="fields"),
    path("fields/<uuid:field_id>/", views.field_detail, name="field-detail"),
    path(
        "fields/<uuid:field_id>/boundary-suggestions/",
        views.boundary_suggestion,
        name="boundary-suggestion",
    ),
    path("analyses/", views.analyses_collection, name="analyses"),
    path("analyses/<uuid:analysis_id>/", views.analysis_detail, name="analysis-detail"),
    path(
        "analyses/<uuid:analysis_id>/recluster/",
        views.analysis_recluster,
        name="analysis-recluster",
    ),
    path("agent-sessions/", views.agent_sessions_collection, name="agent-sessions"),
    path(
        "agent-sessions/<uuid:session_id>/",
        views.agent_session_detail,
        name="agent-session-detail",
    ),
    path(
        "agent-sessions/<uuid:session_id>/turns/",
        views.agent_session_turns,
        name="agent-session-turns",
    ),
    path("feedback/", views.feedback_collection, name="feedback"),
    path("fixtures/", views.fixtures_index, name="fixtures"),
    path("fixtures/<slug:fixture_name>/", views.fixture_detail, name="fixture-detail"),
]
