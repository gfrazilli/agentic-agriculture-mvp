from functools import lru_cache

from django.conf import settings

from agriculture.adapters import (
    CloudTasksQueue,
    FirestoreRepository,
    GCSArtifactStore,
    InMemoryAgricultureRepository,
    InMemoryArtifactStore,
    InMemoryTaskQueue,
)
from agriculture.ports.artifacts import ArtifactStore
from agriculture.ports.boundaries import BoundaryProvider
from agriculture.ports.repositories import AgricultureRepository
from agriculture.ports.tasks import TaskQueue
from agriculture.services.application import AgricultureService


@lru_cache(maxsize=1)
def get_repository() -> AgricultureRepository:
    if settings.PERSISTENCE_BACKEND == "memory":
        return InMemoryAgricultureRepository()
    if settings.PERSISTENCE_BACKEND == "firestore":
        return FirestoreRepository(
            project=settings.GOOGLE_CLOUD_PROJECT,
            database=settings.FIRESTORE_DATABASE,
        )
    raise RuntimeError(f"Unsupported persistence backend: {settings.PERSISTENCE_BACKEND}")


@lru_cache(maxsize=1)
def get_artifact_store() -> ArtifactStore:
    if settings.ARTIFACT_BACKEND == "memory":
        return InMemoryArtifactStore()
    if settings.ARTIFACT_BACKEND == "gcs":
        return GCSArtifactStore(
            settings.GCS_BUCKET,
            project=settings.GOOGLE_CLOUD_PROJECT,
            default_metadata={"component": "agentic-agriculture"},
        )
    raise RuntimeError(f"Unsupported artifact backend: {settings.ARTIFACT_BACKEND}")


@lru_cache(maxsize=1)
def get_task_queue() -> TaskQueue:
    if settings.TASK_BACKEND == "memory":
        return InMemoryTaskQueue()
    if settings.TASK_BACKEND == "cloud_tasks":
        return CloudTasksQueue(
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.CLOUD_TASKS_LOCATION,
            queue=settings.CLOUD_TASKS_QUEUE,
            target_base_url=settings.CLOUD_TASKS_BASE_URL,
            shared_secret=settings.CLOUD_TASKS_SHARED_SECRET,
            oidc_service_account_email=settings.CLOUD_TASKS_SERVICE_ACCOUNT or None,
            oidc_audience=settings.CLOUD_TASKS_BASE_URL,
        )
    raise RuntimeError(f"Unsupported task backend: {settings.TASK_BACKEND}")


@lru_cache(maxsize=1)
def get_boundary_provider() -> BoundaryProvider | None:
    if settings.BOUNDARY_BACKEND == "fixture":
        return None
    if settings.BOUNDARY_BACKEND == "geospatial":
        from geospatial.boundary_service import EarthSearchBoundaryProvider

        return EarthSearchBoundaryProvider()
    raise RuntimeError(f"Unsupported boundary backend: {settings.BOUNDARY_BACKEND}")


@lru_cache(maxsize=1)
def get_analysis_pipeline():
    """Build the asynchronous Sentinel worker only when explicitly enabled."""

    if settings.ANALYSIS_PIPELINE_BACKEND == "disabled":
        return None
    if settings.ANALYSIS_PIPELINE_BACKEND == "sentinel":
        from geospatial.pipeline import AnalysisPipeline

        return AnalysisPipeline(
            get_repository(),
            get_artifact_store(),
            target_scene_count=settings.ANALYSIS_TARGET_SCENE_COUNT,
            max_dimension=settings.ANALYSIS_MAX_DIMENSION,
        )
    raise RuntimeError(
        f"Unsupported analysis pipeline backend: {settings.ANALYSIS_PIPELINE_BACKEND}"
    )


@lru_cache(maxsize=1)
def get_agriculture_service() -> AgricultureService:
    return AgricultureService(
        get_repository(),
        get_task_queue(),
        boundary_provider=get_boundary_provider(),
    )


def reset_container() -> None:
    """Clear process-local singletons; intended for tests and settings overrides."""

    get_agriculture_service.cache_clear()
    get_analysis_pipeline.cache_clear()
    get_boundary_provider.cache_clear()
    get_task_queue.cache_clear()
    get_artifact_store.cache_clear()
    get_repository.cache_clear()
