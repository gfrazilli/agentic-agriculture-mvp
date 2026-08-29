from django.urls import path

from agriculture.internal import views

app_name = "agriculture_internal"

urlpatterns = [
    path("analyses", views.receive_analysis_task, name="analysis-task"),
]
