"""Boundary-suggestion provider contract."""

from dataclasses import dataclass
from typing import Protocol

from agriculture.schemas import BoundarySource, Field, GeoJSONPolygon


@dataclass(frozen=True, slots=True)
class BoundaryProposal:
    boundary: GeoJSONPolygon
    estimated_area_ha: float
    confidence: float
    source: BoundarySource


class BoundaryProvider(Protocol):
    def suggest(self, field: Field) -> BoundaryProposal: ...
