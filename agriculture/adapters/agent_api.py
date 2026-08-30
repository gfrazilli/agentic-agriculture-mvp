"""Authenticated client for the private Google ADK Cloud Run service.

The browser never receives the private service URL or a Google identity token.
This adapter keeps that trust boundary in Django, creates one ADK session for
each public ``AgentSession`` and returns only the final plain-text response plus
an intentionally small execution trace.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Self
from urllib.parse import quote, urlsplit

import httpx

from agentic_agriculture.auth import CloudRunIDTokenHeaderProvider

DEFAULT_AGENT_APP_NAME = "agentic_agriculture"
DEFAULT_AGENT_MODEL = "gemini-3.5-flash"
MAX_AGENT_REPLY_CHARS = 20_000
_APP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_MARKDOWN_FENCE_PATTERN = re.compile(r"(?m)^[ \t]*```[^\n]*$")
_MARKDOWN_RULE_PATTERN = re.compile(r"(?m)^[ \t]{0,3}(?:[-*_][ \t]*){3,}$")
_MARKDOWN_PREFIX_PATTERN = re.compile(r"(?m)^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?|[-+*][ \t]+)")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]\n]+)]\(([^)\n]+)\)")
_MARKDOWN_STRONG_PATTERN = re.compile(r"(?<!\w)\*\*(\S(?:.*?\S)?)\*\*(?!\w)")

HeaderProvider = Callable[[], Mapping[str, str]]


class AgentAPIError(RuntimeError):
    """Base class for failures while communicating with Google ADK."""


class AgentAPIConfigurationError(AgentAPIError, ValueError):
    """Raised before I/O when the private service configuration is unsafe."""


class AgentAPIUnavailableError(AgentAPIError):
    """Raised when the private service or its Cloud Run identity is unavailable."""


class AgentAPIProtocolError(AgentAPIError):
    """Raised when ADK returns a successful but unusable response."""


@dataclass(frozen=True, slots=True)
class AgentAPIConfig:
    """Validated web-to-ADK service configuration."""

    base_url: str
    audience: str | None = None
    timeout_seconds: float = 90.0
    app_name: str = DEFAULT_AGENT_APP_NAME
    model: str = DEFAULT_AGENT_MODEL

    def __post_init__(self) -> None:
        base_url = _validate_service_origin(self.base_url, name="AGENT_API_URL")
        object.__setattr__(self, "base_url", base_url)

        audience = self.audience.strip() if self.audience else None
        if audience is not None:
            audience = _validate_service_origin(audience, name="AGENT_API_AUDIENCE")
            if audience != base_url:
                raise AgentAPIConfigurationError(
                    "AGENT_API_AUDIENCE must equal the AGENT_API_URL service origin."
                )
            object.__setattr__(self, "audience", audience)

        hostname = urlsplit(base_url).hostname or ""
        if hostname.endswith(".run.app") and audience is None:
            raise AgentAPIConfigurationError(
                "AGENT_API_AUDIENCE is required for a private Cloud Run service."
            )

        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise AgentAPIConfigurationError("AGENT_API_TIMEOUT_SECONDS must be a number.")
        if not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 300:
            raise AgentAPIConfigurationError(
                "AGENT_API_TIMEOUT_SECONDS must be between 1 and 300 seconds."
            )
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        app_name = self.app_name.strip()
        if _APP_NAME_PATTERN.fullmatch(app_name) is None:
            raise AgentAPIConfigurationError(
                "AGENT_APP_NAME must contain lowercase letters, digits or underscores."
            )
        object.__setattr__(self, "app_name", app_name)

        model = self.model.strip()
        if not model:
            raise AgentAPIConfigurationError("AGENT_MODEL must not be empty.")
        object.__setattr__(self, "model", model)

    @classmethod
    def from_django_settings(cls) -> AgentAPIConfig:
        """Read settings lazily so normal imports and management commands stay I/O-free."""

        from django.conf import settings

        base_url = str(settings.AGENT_API_URL).strip()
        if not base_url:
            raise AgentAPIConfigurationError("AGENT_API_URL is not configured.")
        audience = str(settings.AGENT_API_AUDIENCE).strip() or None
        return cls(
            base_url=base_url,
            audience=audience,
            timeout_seconds=settings.AGENT_API_TIMEOUT_SECONDS,
            app_name=settings.AGENT_APP_NAME,
            model=settings.AGENT_MODEL,
        )


@dataclass(frozen=True, slots=True)
class AgentTurnContext:
    """Trusted identifiers supplied by Django, never by the turn request body."""

    execution_id: str
    session_id: str
    actor_id: str
    language: Literal["pt-BR", "en"]
    channel: str
    field_id: str | None = None
    analysis_id: str | None = None

    def __post_init__(self) -> None:
        if self.language not in {"pt-BR", "en"}:
            raise AgentAPIConfigurationError(
                "Agent session language must be either 'pt-BR' or 'en'."
            )


@dataclass(frozen=True, slots=True)
class AgentTurnReply:
    """Narrow, JSON-friendly response extracted from ADK events."""

    text: str
    model: str
    agents: tuple[str, ...]
    tools: tuple[str, ...]


class AgentAPIClient:
    """Synchronous, connection-pooled client for the ADK API server."""

    def __init__(
        self,
        config: AgentAPIConfig,
        *,
        header_provider: HeaderProvider | None = None,
        token_fetcher: Callable[[str], str] | None = None,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if client is not None and transport is not None:
            raise AgentAPIConfigurationError("Pass either client or transport, not both.")
        if header_provider is not None and token_fetcher is not None:
            raise AgentAPIConfigurationError(
                "Pass either header_provider or token_fetcher, not both."
            )

        self.config = config
        if header_provider is not None:
            self._header_provider = header_provider
        elif config.audience is not None:
            provider_kwargs = {}
            if token_fetcher is not None:
                provider_kwargs["token_fetcher"] = token_fetcher
            self._header_provider = CloudRunIDTokenHeaderProvider(
                config.audience,
                **provider_kwargs,
            )
        else:
            self._header_provider = None

        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=f"{config.base_url}/",
            timeout=httpx.Timeout(config.timeout_seconds),
            transport=transport,
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "agentic-agriculture-web/0.1",
            },
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def run_turn(self, message: str, context: AgentTurnContext) -> AgentTurnReply:
        """Create/reuse an ADK session and synchronously execute one text turn."""

        adk_user_id = _actor_user_id(context.actor_id)
        self._ensure_session(adk_user_id=adk_user_id, context=context)

        state = _session_state(context)
        trusted_context = _trusted_context_text(state)
        payload = {
            "app_name": self.config.app_name,
            "user_id": adk_user_id,
            "session_id": context.session_id,
            "new_message": {
                "role": "user",
                "parts": [
                    {"text": trusted_context},
                    {"text": message},
                ],
            },
            "streaming": False,
            "state_delta": state,
            "custom_metadata": {
                "channel": "django-web",
                "execution_id": context.execution_id,
            },
        }
        response = self._request("POST", "run", json_payload=payload)
        events = _decode_json(response, expected="array")
        return _extract_reply(events, fallback_model=self.config.model)

    def _ensure_session(self, *, adk_user_id: str, context: AgentTurnContext) -> None:
        app_name = quote(self.config.app_name, safe="")
        user_id = quote(adk_user_id, safe="")
        session_id = quote(context.session_id, safe="")
        detail_path = f"apps/{app_name}/users/{user_id}/sessions/{session_id}"

        response = self._request("GET", detail_path, allowed_statuses={404})
        if response.status_code == 200:
            _validate_session_response(response, expected_id=context.session_id)
            return

        collection_path = f"apps/{app_name}/users/{user_id}/sessions"
        created = self._request(
            "POST",
            collection_path,
            json_payload={
                "session_id": context.session_id,
                "state": _session_state(context),
            },
            allowed_statuses={409},
        )
        if created.status_code == 409:
            # Another web request can win the create race. Confirm the exact
            # session before reusing it instead of trusting the conflict body.
            existing = self._request("GET", detail_path)
            _validate_session_response(existing, expected_id=context.session_id)
            return
        _validate_session_response(created, expected_id=context.session_id)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Mapping[str, Any] | None = None,
        allowed_statuses: set[int] | None = None,
    ) -> httpx.Response:
        allowed_statuses = allowed_statuses or set()
        try:
            headers = dict(self._header_provider()) if self._header_provider else {}
            response = self._client.request(
                method,
                path,
                json=json_payload,
                headers=headers,
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise AgentAPIUnavailableError(
                f"Private agent request failed ({type(exc).__name__})."
            ) from exc

        if response.status_code in allowed_statuses:
            return response
        if not 200 <= response.status_code < 300:
            raise AgentAPIUnavailableError(
                f"Private agent service returned HTTP {response.status_code}."
            )
        return response


def _validate_service_origin(value: str, *, name: str) -> str:
    normalized = value.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
        _ = parsed.port
    except ValueError:
        raise AgentAPIConfigurationError(f"{name} contains an invalid port.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AgentAPIConfigurationError(f"{name} must be an absolute HTTP(S) origin.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AgentAPIConfigurationError(
            f"{name} cannot contain credentials, a query or a fragment."
        )
    if parsed.path not in {"", "/"}:
        raise AgentAPIConfigurationError(f"{name} must be a service origin without a path.")
    if parsed.scheme == "http" and not _is_internal_hostname(parsed.hostname):
        raise AgentAPIConfigurationError(f"{name} must use HTTPS outside a local network.")
    return normalized


def _is_internal_hostname(hostname: str) -> bool:
    hostname = hostname.lower()
    return (
        hostname in {"localhost", "127.0.0.1", "::1"}
        or "." not in hostname
        or hostname.endswith(".local")
    )


def _actor_user_id(actor_id: str) -> str:
    """Produce a stable, opaque ADK user id without exposing the login name."""

    digest = hashlib.sha256(actor_id.encode("utf-8")).hexdigest()[:32]
    return f"web-{digest}"


def _session_state(context: AgentTurnContext) -> dict[str, str]:
    state = {
        "language": context.language,
        "channel": context.channel,
    }
    if context.field_id is not None:
        state["field_id"] = context.field_id
    if context.analysis_id is not None:
        state["analysis_id"] = context.analysis_id
    return state


def _trusted_context_text(state: Mapping[str, str]) -> str:
    language = state["language"]
    if language == "en":
        lines = [
            "Mandatory response contract defined by the trusted application:",
            "- Respond exclusively in English, regardless of the question or conversation history.",
            (
                "- Return plain text only, without Markdown, HTML, headings, lists, "
                "or formatting marks."
            ),
            "- Be direct and operational; use no more than 180 words.",
            "Preserve these trusted identifiers:",
            *(f"- {key}: {value}" for key, value in state.items()),
            "The next part contains only the user's question.",
        ]
    else:
        lines = [
            "Contrato obrigatório definido pelo aplicativo confiável:",
            (
                "- Responda exclusivamente em português do Brasil, independentemente "
                "da pergunta ou do histórico."
            ),
            (
                "- Entregue apenas texto simples, sem Markdown, HTML, cabeçalhos, "
                "listas ou marcas de formatação."
            ),
            "- Seja direto e operacional; use no máximo 180 palavras.",
            "Preserve estes identificadores confiáveis:",
            *(f"- {key}: {value}" for key, value in state.items()),
            "A próxima parte contém somente a pergunta do usuário.",
        ]
    return "\n".join(lines)


def _decode_json(response: httpx.Response, *, expected: str) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AgentAPIProtocolError("The private agent returned invalid JSON.") from exc
    if expected == "array" and not isinstance(payload, list):
        raise AgentAPIProtocolError("The private agent response must be a JSON array.")
    if expected == "object" and not isinstance(payload, Mapping):
        raise AgentAPIProtocolError("The private agent response must be a JSON object.")
    return payload


def _validate_session_response(response: httpx.Response, *, expected_id: str) -> None:
    payload = _decode_json(response, expected="object")
    if payload.get("id") != expected_id:
        raise AgentAPIProtocolError("The private agent returned a different session id.")


def _extract_reply(events: Any, *, fallback_model: str) -> AgentTurnReply:
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise AgentAPIProtocolError("The private agent event list is invalid.")

    agents: list[str] = []
    tools: list[str] = []
    candidates: list[tuple[str, str | None]] = []

    for event in events:
        if not isinstance(event, Mapping):
            raise AgentAPIProtocolError("The private agent returned an invalid event.")
        author = event.get("author")
        if isinstance(author, str) and author and author != "user":
            _append_unique(agents, author)

        content = event.get("content")
        if not isinstance(content, Mapping):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue

        event_text: list[str] = []
        has_tool_part = False
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            function_call = part.get("functionCall")
            function_response = part.get("functionResponse")
            for tool_part in (function_call, function_response):
                if isinstance(tool_part, Mapping):
                    tool_name = tool_part.get("name")
                    if isinstance(tool_name, str) and tool_name:
                        _append_unique(tools, tool_name)
                    has_tool_part = True
            text = part.get("text")
            thought = part.get("thought")
            if isinstance(text, str) and text.strip() and thought is not True:
                event_text.append(text.strip())

        if event_text and not has_tool_part and event.get("partial") is not True:
            model = event.get("modelVersion")
            candidates.append(("\n".join(event_text), model if isinstance(model, str) else None))

    if not candidates:
        raise AgentAPIProtocolError("The private agent returned no final text response.")

    text, event_model = candidates[-1]
    text = _plain_text(text[:MAX_AGENT_REPLY_CHARS])
    if not text:
        raise AgentAPIProtocolError("The private agent returned an empty final response.")
    return AgentTurnReply(
        text=text,
        model=event_model or fallback_model,
        agents=tuple(agents),
        tools=tuple(tools),
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _plain_text(value: str) -> str:
    """Remove common model-authored Markdown while preserving readable text."""

    value = _MARKDOWN_FENCE_PATTERN.sub("", value)
    value = _MARKDOWN_RULE_PATTERN.sub("", value)
    value = _MARKDOWN_PREFIX_PATTERN.sub("", value)
    value = _MARKDOWN_LINK_PATTERN.sub(r"\1 (\2)", value)
    value = _MARKDOWN_STRONG_PATTERN.sub(r"\1", value)
    value = value.replace("`", "")
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
