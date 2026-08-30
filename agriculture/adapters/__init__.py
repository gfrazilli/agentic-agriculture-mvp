"""Concrete local and Google Cloud adapters."""

from agriculture.adapters.agent_api import (
    AgentAPIClient,
    AgentAPIConfig,
    AgentAPIConfigurationError,
    AgentAPIProtocolError,
    AgentAPIUnavailableError,
    AgentTurnContext,
    AgentTurnReply,
)
from agriculture.adapters.artifacts import GCSArtifactStore, InMemoryArtifactStore
from agriculture.adapters.firestore import FirestoreRepository
from agriculture.adapters.memory import InMemoryAgricultureRepository
from agriculture.adapters.optional import MissingGoogleDependency
from agriculture.adapters.tasks import CloudTasksQueue, InMemoryTaskQueue

__all__ = [
    "AgentAPIClient",
    "AgentAPIConfig",
    "AgentAPIConfigurationError",
    "AgentAPIProtocolError",
    "AgentAPIUnavailableError",
    "AgentTurnContext",
    "AgentTurnReply",
    "CloudTasksQueue",
    "FirestoreRepository",
    "GCSArtifactStore",
    "InMemoryAgricultureRepository",
    "InMemoryArtifactStore",
    "InMemoryTaskQueue",
    "MissingGoogleDependency",
]
