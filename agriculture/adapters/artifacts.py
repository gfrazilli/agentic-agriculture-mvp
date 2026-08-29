"""In-memory and Google Cloud Storage artifact adapters."""

from collections.abc import Mapping
from pathlib import PurePosixPath
from threading import RLock
from typing import Any

from agriculture.adapters.optional import load_google_module
from agriculture.ports.artifacts import ArtifactRef


def _valid_key(key: str) -> str:
    normalized = key.strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("artifact key must be a normalized relative POSIX path")
    return normalized


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._objects: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> ArtifactRef:
        key = _valid_key(key)
        if not content_type.strip():
            raise ValueError("content_type must not be empty")
        payload = bytes(data)
        with self._lock:
            self._objects[key] = (payload, content_type)
        return ArtifactRef(
            key=key, uri=f"memory:///{key}", content_type=content_type, size=len(payload)
        )

    def get_bytes(self, key: str) -> bytes:
        key = _valid_key(key)
        with self._lock:
            try:
                payload, _content_type = self._objects[key]
            except KeyError:
                raise KeyError(f"Artifact {key!r} does not exist.") from None
            return bytes(payload)

    def exists(self, key: str) -> bool:
        key = _valid_key(key)
        with self._lock:
            return key in self._objects

    def delete(self, key: str) -> None:
        key = _valid_key(key)
        with self._lock:
            self._objects.pop(key, None)


class GCSArtifactStore:
    """Google Cloud Storage implementation; imports its SDK only when instantiated."""

    def __init__(
        self,
        bucket_name: str,
        *,
        project: str | None = None,
        client: Any | None = None,
        default_metadata: Mapping[str, str] | None = None,
    ) -> None:
        storage = load_google_module("google.cloud.storage", "google-cloud-storage")
        if not bucket_name.strip():
            raise ValueError("bucket_name must not be empty")
        self._client = client or storage.Client(project=project)
        self._bucket = self._client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._default_metadata = dict(default_metadata or {})

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> ArtifactRef:
        key = _valid_key(key)
        if not content_type.strip():
            raise ValueError("content_type must not be empty")
        payload = bytes(data)
        blob = self._bucket.blob(key)
        if self._default_metadata:
            blob.metadata = self._default_metadata
        blob.upload_from_string(payload, content_type=content_type)
        return ArtifactRef(
            key=key,
            uri=f"gs://{self._bucket_name}/{key}",
            content_type=content_type,
            size=len(payload),
        )

    def get_bytes(self, key: str) -> bytes:
        return bytes(self._bucket.blob(_valid_key(key)).download_as_bytes())

    def exists(self, key: str) -> bool:
        return bool(self._bucket.blob(_valid_key(key)).exists(client=self._client))

    def delete(self, key: str) -> None:
        blob = self._bucket.blob(_valid_key(key))
        if blob.exists(client=self._client):
            blob.delete()
