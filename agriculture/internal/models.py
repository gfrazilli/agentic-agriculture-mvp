from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AnalysisTaskInput(BaseModel):
    """Immutable payload emitted when an analysis request is accepted."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    analysis_id: UUID
    field_id: UUID
    parent_analysis_id: UUID | None = None
