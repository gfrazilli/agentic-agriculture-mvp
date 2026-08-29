"""Lazy Cloud Run service-to-service authentication for the MCP client."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock
from typing import Any

IDTokenFetcher = Callable[[str], str]


def fetch_google_id_token(audience: str) -> str:
    """Fetch a Google-signed ID token using Application Default Credentials."""

    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token

    return fetch_id_token(Request(), audience)


class CloudRunIDTokenHeaderProvider:
    """Mint and briefly cache a Cloud Run ID token when ADK opens an MCP session.

    Construction is network-free. The first invocation uses ADC and later calls
    refresh before the normal one-hour token lifetime is reached.
    """

    def __init__(
        self,
        audience: str,
        *,
        token_fetcher: IDTokenFetcher = fetch_google_id_token,
        clock: Callable[[], float] = time.monotonic,
        cache_seconds: float = 40 * 60,
    ) -> None:
        if not audience.strip():
            raise ValueError("Cloud Run audience must not be empty.")
        if cache_seconds <= 0:
            raise ValueError("cache_seconds must be greater than zero.")
        self._audience = audience.strip()
        self._token_fetcher = token_fetcher
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._token: str | None = None
        self._refresh_at = 0.0
        self._lock = Lock()

    def __call__(self, _readonly_context: Any = None) -> dict[str, str]:
        now = self._clock()
        with self._lock:
            if self._token is None or now >= self._refresh_at:
                token = self._token_fetcher(self._audience).strip()
                if not token or "\r" in token or "\n" in token:
                    raise ValueError("The ID token provider returned an invalid token.")
                self._token = token
                self._refresh_at = now + self._cache_seconds
            return {"Authorization": f"Bearer {self._token}"}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(audience={self._audience!r})"
