from django.conf import settings
from django.core.checks import Error, Tags, register

from agriculture.internal.security import task_secret_is_valid


def backend_configuration() -> dict[str, bool]:
    persistence_valid = settings.PERSISTENCE_BACKEND in {"memory", "firestore"}
    artifacts_valid = settings.ARTIFACT_BACKEND in {"memory", "gcs"}
    tasks_valid = settings.TASK_BACKEND in {"memory", "cloud_tasks"}
    boundary_valid = settings.BOUNDARY_BACKEND in {"fixture", "geospatial"}
    pipeline_valid = settings.ANALYSIS_PIPELINE_BACKEND in {"disabled", "sentinel"}
    pipeline_limits_valid = (
        2 <= settings.ANALYSIS_TARGET_SCENE_COUNT <= 12
        and 64 <= settings.ANALYSIS_MAX_DIMENSION <= 1024
    )

    firestore_ready = settings.PERSISTENCE_BACKEND != "firestore" or bool(
        settings.GOOGLE_CLOUD_PROJECT
    )
    gcs_ready = settings.ARTIFACT_BACKEND != "gcs" or bool(
        settings.GOOGLE_CLOUD_PROJECT and settings.GCS_BUCKET
    )
    tasks_ready = settings.TASK_BACKEND != "cloud_tasks" or bool(
        settings.GOOGLE_CLOUD_PROJECT
        and settings.CLOUD_TASKS_LOCATION
        and settings.CLOUD_TASKS_QUEUE
        and settings.CLOUD_TASKS_BASE_URL
        and (not settings.IS_PRODUCTION or settings.CLOUD_TASKS_BASE_URL.startswith("https://"))
        and task_secret_is_valid(settings.CLOUD_TASKS_SHARED_SECRET)
    )
    production_backends = not settings.IS_PRODUCTION or (
        settings.PERSISTENCE_BACKEND == "firestore"
        and settings.ARTIFACT_BACKEND == "gcs"
        and settings.TASK_BACKEND == "cloud_tasks"
        and settings.BOUNDARY_BACKEND == "geospatial"
        and settings.ANALYSIS_PIPELINE_BACKEND == "sentinel"
    )

    return {
        "backend_names": (
            persistence_valid
            and artifacts_valid
            and tasks_valid
            and boundary_valid
            and pipeline_valid
        ),
        "geospatial_processing": boundary_valid and pipeline_valid and pipeline_limits_valid,
        "firestore": firestore_ready,
        "cloud_storage": gcs_ready,
        "cloud_tasks": tasks_ready,
        "production_backends": production_backends,
    }


@register(Tags.security)
def check_cloud_backends(app_configs, **kwargs):  # noqa: ARG001
    checks = backend_configuration()
    errors: list[Error] = []

    if not checks["backend_names"]:
        errors.append(
            Error(
                "One or more agriculture backend names are invalid.",
                hint=(
                    "Use memory/firestore, memory/gcs and "
                    "memory/cloud_tasks for the respective backend settings."
                ),
                id="agriculture.E001",
            )
        )
    if not checks["production_backends"]:
        errors.append(
            Error(
                "Production cannot use in-memory agriculture backends.",
                hint="Use Firestore, Cloud Storage and Cloud Tasks in production.",
                id="agriculture.E002",
            )
        )
    if not checks["firestore"]:
        errors.append(
            Error(
                "Firestore requires GOOGLE_CLOUD_PROJECT.",
                id="agriculture.E003",
            )
        )
    if not checks["cloud_storage"]:
        errors.append(
            Error(
                "Cloud Storage requires GOOGLE_CLOUD_PROJECT and GCS_BUCKET.",
                id="agriculture.E004",
            )
        )
    if not checks["cloud_tasks"]:
        errors.append(
            Error(
                (
                    "Cloud Tasks requires project, location, queue, a secure handler URL and "
                    "a shared secret of at least 32 characters."
                ),
                id="agriculture.E005",
            )
        )
    if not checks["geospatial_processing"]:
        errors.append(
            Error(
                "Geospatial processing settings are invalid.",
                hint=(
                    "Use fixture/geospatial for BOUNDARY_BACKEND, disabled/sentinel for "
                    "ANALYSIS_PIPELINE_BACKEND, 2-12 scenes and a 64-1024 pixel dimension."
                ),
                id="agriculture.E006",
            )
        )
    return errors
