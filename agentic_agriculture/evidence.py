"""Deterministic, JSON-safe evidence projection used by Gemini tools."""

from __future__ import annotations

from typing import Any

from agriculture.schemas import Analysis, Field, Zone

INTERPRETATION_LIMITS_PT = (
    "Compara somente a variabilidade espacial relativa dentro da área plantada.",
    "Não identifica causa, praga, doença, solo, falta de água, tratamento ou produtividade.",
    "Uma decisão agronômica exige verificação em campo e contexto profissional.",
)
INTERPRETATION_LIMITS_EN = (
    "Compares only relative spatial variability within the field.",
    "Does not identify a cause, pest, disease, soil issue, water need, treatment, or yield.",
    "An agronomic decision requires field verification and professional context.",
)


def _json(model: Any, *, exclude_none: bool = False) -> Any:
    return model.model_dump(mode="json", exclude_none=exclude_none)


def field_evidence(field: Field) -> dict[str, Any]:
    """Project a field into the exact context the conversational layer may cite."""

    return {
        "evidence_type": "field_context",
        "schema_version": field.schema_version,
        "source": "agriculture_repository",
        "field": {
            "id": str(field.id),
            "name": field.name,
            "crop": field.crop,
            "season_start": field.season_start.isoformat(),
            "season_end": field.season_end.isoformat(),
            "estimated_area_ha": field.estimated_area_ha,
            "reference_location": _json(field.reference_location),
            "boundary_confirmed": field.boundary_confirmed,
            "boundary": _json(field.boundary) if field.boundary is not None else None,
            "updated_at": field.updated_at.isoformat(),
        },
        "interpretation_limits_pt": INTERPRETATION_LIMITS_PT,
        "interpretation_limits_en": INTERPRETATION_LIMITS_EN,
    }


def analysis_summary(analysis: Analysis) -> dict[str, Any]:
    """Return a compact status record suitable for lists and routing."""

    result = analysis.result
    return {
        "analysis_id": str(analysis.id),
        "field_id": str(analysis.field_id),
        "status": analysis.status.value,
        "progress": {
            "percent": analysis.progress.percent,
            "stage": analysis.progress.stage.value,
            "message_pt": analysis.progress.message_pt,
            "message_en": analysis.progress.message_en,
            "updated_at": analysis.progress.updated_at.isoformat(),
        },
        "requested_zone_count": analysis.requested_zone_count,
        "selected_zone_count": result.selected_zone_count if result is not None else None,
        "scene_count": len(result.scenes) if result is not None else 0,
        "created_at": analysis.created_at.isoformat(),
        "updated_at": analysis.updated_at.isoformat(),
    }


def _scene_evidence(scene: Any) -> dict[str, Any]:
    return {
        "scene_id": scene.scene_id,
        "captured_at": scene.captured_at.isoformat(),
        "cloud_cover_percent": scene.cloud_cover_percent,
        "field_indices": _json(scene.field_indices),
        "preview_uri": scene.preview_uri,
    }


def _zone_overview(zone: Zone) -> dict[str, Any]:
    return {
        "zone_id": zone.zone_id,
        "relative_label": zone.relative_label.value,
        "area_ha": zone.area_ha,
        "area_percent": zone.area_percent,
        "trajectory": [_json(point) for point in zone.trajectory],
        "summary_pt": zone.summary_pt,
        "summary_en": zone.summary_en,
    }


def analysis_evidence(analysis: Analysis) -> dict[str, Any]:
    """Project an analysis without fabricating evidence for unfinished work."""

    envelope: dict[str, Any] = {
        "evidence_type": "analysis",
        "schema_version": analysis.schema_version,
        "source": "agriculture_repository",
        "analysis": analysis_summary(analysis),
        "interpretation_limits_pt": INTERPRETATION_LIMITS_PT,
        "interpretation_limits_en": INTERPRETATION_LIMITS_EN,
    }
    if analysis.error is not None:
        envelope["error"] = _json(analysis.error)
    if analysis.result is None:
        envelope["result_available"] = False
        return envelope

    result = analysis.result
    envelope.update(
        {
            "result_available": True,
            "result": {
                "mode": result.mode.value,
                "generated_at": result.generated_at.isoformat(),
                "selected_zone_count": result.selected_zone_count,
                "scenes": [_scene_evidence(scene) for scene in result.scenes],
                "zones": [_zone_overview(zone) for zone in result.zones],
                "scope": _json(result.scope),
                "provenance": _json(result.provenance),
                "artifacts": _json(result.artifacts),
            },
        }
    )
    return envelope


def zone_evidence(analysis: Analysis, zone_id: str) -> dict[str, Any] | None:
    """Return exact evidence for one zone, including its deterministic geometry."""

    if analysis.result is None:
        return None
    zone = next((item for item in analysis.result.zones if item.zone_id == zone_id), None)
    if zone is None:
        return None
    return {
        "evidence_type": "analysis_zone",
        "schema_version": analysis.schema_version,
        "source": "agriculture_repository",
        "analysis_id": str(analysis.id),
        "field_id": str(analysis.field_id),
        "zone": {
            **_zone_overview(zone),
            "boundary": _json(zone.boundary),
        },
        "scene_ids": list(analysis.result.provenance.scene_ids),
        "provenance": _json(analysis.result.provenance),
        "scope": _json(analysis.result.scope),
        "interpretation_limits_pt": INTERPRETATION_LIMITS_PT,
        "interpretation_limits_en": INTERPRETATION_LIMITS_EN,
    }
