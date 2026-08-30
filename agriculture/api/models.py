import re
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import Field as PydanticField

from agriculture.domain import AnalysisStatus
from agriculture.schemas import (
    AgentSessionChannel,
    AgentSessionStatus,
    FeedbackRating,
    GeoJSONPoint,
    GeoJSONPolygon,
)


class APIInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class FieldCreateInput(APIInput):
    name: Annotated[str, PydanticField(min_length=1, max_length=120)]
    crop: Annotated[str, PydanticField(min_length=1, max_length=80)]
    season_start: date
    season_end: date
    estimated_area_ha: Annotated[float, PydanticField(gt=0, le=500)]
    reference_location: GeoJSONPoint

    @model_validator(mode="after")
    def validate_season(self):
        duration = (self.season_end - self.season_start).days
        if not 1 <= duration <= 365:
            raise ValueError("The crop season must contain between 1 and 365 days.")
        return self


class FieldPatchInput(APIInput):
    name: Annotated[str, PydanticField(min_length=1, max_length=120)] | None = None
    crop: Annotated[str, PydanticField(min_length=1, max_length=80)] | None = None
    season_start: date | None = None
    season_end: date | None = None
    estimated_area_ha: Annotated[float, PydanticField(gt=0, le=500)] | None = None
    reference_location: GeoJSONPoint | None = None
    boundary: GeoJSONPolygon | None = None
    boundary_confirmed: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be supplied.")
        return self


class EmptyInput(APIInput):
    pass


class AnalysisCreateInput(APIInput):
    field_id: UUID
    requested_zone_count: Annotated[int, PydanticField(ge=2, le=7)] | None = None


class ReclusterInput(APIInput):
    zone_count: Annotated[int, PydanticField(ge=2, le=7)]


class AgentSessionCreateInput(APIInput):
    language: Literal["pt-BR", "en"] = "pt-BR"
    channel: AgentSessionChannel = AgentSessionChannel.TEXT
    field_id: UUID | None = None
    analysis_id: UUID | None = None


class AgentSessionPatchInput(APIInput):
    status: AgentSessionStatus | None = None
    field_id: UUID | None = None
    analysis_id: UUID | None = None
    increment_turn_count: bool = False

    @model_validator(mode="after")
    def require_change(self):
        supplied = self.model_fields_set - {"increment_turn_count"}
        if not supplied and not self.increment_turn_count:
            raise ValueError("At least one session change must be supplied.")
        return self


class AgentTurnCreateInput(APIInput):
    message: Annotated[str, PydanticField(min_length=1, max_length=2_000)]

    @field_validator("message")
    @classmethod
    def require_plain_text(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
            raise ValueError("The message cannot contain control characters.")
        # The API contract is text-only. Comparison operators such as ``<``
        # remain valid; only tag-shaped markup is rejected.
        if re.search(r"<\s*/?\s*[A-Za-z][^>]*>", value):
            raise ValueError("HTML markup is not accepted; send plain text.")
        return value


class FeedbackCreateInput(APIInput):
    analysis_id: UUID
    session_id: UUID
    rating: FeedbackRating
    comment: Annotated[str, PydanticField(min_length=1, max_length=500)] | None = None
    zone_id: Annotated[str, PydanticField(pattern=r"^zone-[1-7]$")] | None = None


class InternalAnalysisUpdateInput(APIInput):
    status: AnalysisStatus
    progress_percent: Annotated[int, PydanticField(ge=0, le=100)]
