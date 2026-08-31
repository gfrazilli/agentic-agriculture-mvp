"""Grounded repository and idempotent action tools exposed to ADK specialists."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from agentic_agriculture.evidence import (
    analysis_evidence,
    analysis_summary,
    field_evidence,
    zone_evidence,
)
from agriculture.api.errors import APIError
from agriculture.api.models import AnalysisCreateInput
from agriculture.observability import audit_event, get_audit_logger
from agriculture.ports.repositories import AgricultureRepository
from agriculture.services.application import AgricultureService
from agriculture.services.idempotency import IdempotencyContext

RepositoryProvider = Callable[[], AgricultureRepository]
ServiceProvider = Callable[[], AgricultureService]
_ZONE_ID = re.compile(r"^zone-[1-7]$")
_AGENT_ANALYSIS_ACTOR = "agentic-agriculture-adk"
logger = get_audit_logger(__name__)


def _bootstrap_django() -> None:
    """Initialize Django lazily for standalone ADK processes."""

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()


def django_repository_provider() -> AgricultureRepository:
    """Resolve Django's configured repository only when a tool is actually called."""

    _bootstrap_django()
    from agriculture.container import get_repository

    return get_repository()


def django_service_provider() -> AgricultureService:
    """Resolve the configured application service only when an action is requested."""

    _bootstrap_django()
    from agriculture.container import get_agriculture_service

    return get_agriculture_service()


def _identifier(value: str, *, name: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 160:
        raise ValueError(f"{name} is invalid.")
    return normalized


def _not_found(code: str, message_pt: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message_pt": message_pt}}


def _analysis_action_context(
    *,
    field_id: str,
    requested_zone_count: int,
    field_updated_at: datetime,
) -> IdempotencyContext:
    """Bind one agent action to the exact confirmed field state and zone request."""

    material = json.dumps(
        {
            "action": "request_field_analysis",
            "field_id": field_id,
            "field_updated_at": field_updated_at.isoformat(),
            "requested_zone_count": requested_zone_count,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return IdempotencyContext(
        scoped_key=hashlib.sha256(b"agent-action\0" + material).hexdigest(),
        request_digest=hashlib.sha256(material).hexdigest(),
    )


class AgricultureActionTools:
    """Narrow, service-backed mutations that remain safe under model retries."""

    def __init__(self, service_provider: ServiceProvider = django_service_provider) -> None:
        self._service_provider = service_provider

    def request_field_analysis(
        self,
        field_id: str,
        requested_zone_count: int,
    ) -> dict[str, Any]:
        """Solicita uma análise idempotente e retorna o ID para acompanhamento."""

        try:
            normalized = _identifier(field_id, name="field_id")
        except (AttributeError, ValueError):
            audit_event(
                logger,
                "agent_action.request_field_analysis",
                level=logging.WARNING,
                component="agent",
                execution_id=str(uuid4()),
                status="rejected",
                error_code="invalid_field_id",
                error_type="ValidationError",
            )
            return _not_found("invalid_field_id", "O ID da área plantada é inválido.")

        service = self._service_provider()
        try:
            field = service.get_field(normalized)
            payload = AnalysisCreateInput(
                field_id=field.id,
                requested_zone_count=requested_zone_count,
            )
            context = _analysis_action_context(
                field_id=str(field.id),
                requested_zone_count=requested_zone_count,
                field_updated_at=field.updated_at,
            )
            result = service.create_analysis(
                payload,
                context,
                actor_id=_AGENT_ANALYSIS_ACTOR,
            )
        except ValidationError as exc:
            audit_event(
                logger,
                "agent_action.request_field_analysis",
                level=logging.WARNING,
                component="agent",
                execution_id=str(uuid4()),
                field_id=normalized,
                requested_zone_count=requested_zone_count,
                status="rejected",
                error_code="invalid_analysis_request",
                error_type=type(exc).__name__,
            )
            return _not_found(
                "invalid_analysis_request",
                "A quantidade de divisões deve estar entre 2 e 7.",
            )
        except APIError as exc:
            audit_event(
                logger,
                "agent_action.request_field_analysis",
                level=logging.WARNING,
                component="agent",
                execution_id=str(uuid4()),
                field_id=normalized,
                requested_zone_count=requested_zone_count,
                status="rejected",
                error_code=exc.code,
                error_type=type(exc).__name__,
            )
            return {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "status": exc.status,
                },
            }

        data = result.data
        if not isinstance(data, Mapping):
            raise RuntimeError("The analysis service returned an invalid action result.")
        analysis_id = str(data["id"])
        returned_field_id = str(data["field_id"])
        status = str(data["status"])
        audit_event(
            logger,
            "agent_action.request_field_analysis",
            component="agent",
            execution_id=analysis_id,
            field_id=returned_field_id,
            analysis_id=analysis_id,
            requested_zone_count=requested_zone_count,
            status=status,
            replayed=result.replayed,
        )
        return {
            "ok": True,
            "action": "analysis_requested",
            "analysis_id": analysis_id,
            "field_id": returned_field_id,
            "status": status,
            "replayed": result.replayed,
            "follow_up": {
                "tool": "get_analysis_evidence",
                "analysis_id": analysis_id,
            },
        }


class ReadOnlyAgricultureTools:
    """Small, dependency-injected tool surface with no persistence mutations."""

    def __init__(
        self, repository_provider: RepositoryProvider = django_repository_provider
    ) -> None:
        self._repository_provider = repository_provider

    def get_field_context(self, field_id: str) -> dict[str, Any]:
        """Busca dados confirmados de uma área plantada pelo ID, sem alterar o cadastro."""

        try:
            normalized = _identifier(field_id, name="field_id")
        except ValueError:
            return _not_found("invalid_field_id", "O ID da área plantada é inválido.")
        field = self._repository_provider().get_field(normalized)
        if field is None:
            return _not_found("field_not_found", "A área plantada não foi encontrada.")
        return {"ok": True, "evidence": field_evidence(field)}

    def get_analysis_evidence(self, analysis_id: str) -> dict[str, Any]:
        """Busca status, cenas, partes analisadas e proveniência de uma análise pelo ID."""

        try:
            normalized = _identifier(analysis_id, name="analysis_id")
        except ValueError:
            return _not_found("invalid_analysis_id", "O ID da análise é inválido.")
        analysis = self._repository_provider().get_analysis(normalized)
        if analysis is None:
            return _not_found("analysis_not_found", "A análise não foi encontrada.")
        return {"ok": True, "evidence": analysis_evidence(analysis)}

    def get_zone_evidence(self, analysis_id: str, zone_id: str) -> dict[str, Any]:
        """Busca a geometria e a trajetória exatas de uma parte em uma análise concluída."""

        try:
            normalized = _identifier(analysis_id, name="analysis_id")
        except ValueError:
            return _not_found("invalid_analysis_id", "O ID da análise é inválido.")
        zone_id = zone_id.strip()
        if _ZONE_ID.fullmatch(zone_id) is None:
            return _not_found(
                "invalid_zone_id",
                "O identificador da parte deve estar entre zone-1 e zone-7.",
            )
        analysis = self._repository_provider().get_analysis(normalized)
        if analysis is None:
            return _not_found("analysis_not_found", "A análise não foi encontrada.")
        evidence = zone_evidence(analysis, zone_id)
        if evidence is None:
            return _not_found(
                "zone_evidence_unavailable",
                "A parte da área não existe ou a análise ainda não tem resultado.",
            )
        return {"ok": True, "evidence": evidence}

    def list_field_analyses(self, field_id: str, limit: int = 5) -> dict[str, Any]:
        """Lista as análises mais recentes de uma área plantada, sem iniciar processamento."""

        try:
            normalized = _identifier(field_id, name="field_id")
        except ValueError:
            return _not_found("invalid_field_id", "O ID da área plantada é inválido.")
        if not 1 <= limit <= 20:
            return _not_found("invalid_limit", "O limite deve estar entre 1 e 20.")
        analyses = self._repository_provider().list_analyses(normalized)
        analyses.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        selected = analyses[:limit]
        return {
            "ok": True,
            "evidence": {
                "evidence_type": "analysis_list",
                "source": "agriculture_repository",
                "field_id": normalized,
                "count": len(selected),
                "analyses": [analysis_summary(analysis) for analysis in selected],
            },
        }
