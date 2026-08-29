"""Binary artifact storage port."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    key: str
    uri: str
    content_type: str
    size: int


class ArtifactStore(Protocol):
    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> ArtifactRef: ...

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...
