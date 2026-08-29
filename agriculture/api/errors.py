from dataclasses import dataclass, field
from typing import Any


@dataclass
class APIError(Exception):
    code: str
    message: str
    status: int
    details: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
