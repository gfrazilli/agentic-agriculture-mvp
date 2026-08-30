"""Private, non-persistent delivery service for the public contact form."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from django.conf import settings

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
RESEND_EMAILS_URL = "https://api.resend.com/emails"


class ContactServiceError(Exception):
    """Safe base exception a view may handle without exposing provider details."""


class ContactVerificationError(ContactServiceError):
    """The anti-abuse challenge could not be verified."""


class ContactDeliveryError(ContactServiceError):
    """The message could not be accepted by the delivery provider."""


class HTTPClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> httpx.Response: ...


@dataclass(frozen=True, slots=True)
class ContactMessage:
    email: str
    subject: str
    message: str
    turnstile_token: str = ""

    @classmethod
    def from_cleaned_data(cls, cleaned_data: Mapping[str, Any]) -> ContactMessage:
        """Create a message from a valid ``ContactForm.cleaned_data`` mapping."""

        return cls(
            email=str(cleaned_data["email"]),
            subject=str(cleaned_data["subject"]),
            message=str(cleaned_data["message"]),
            turnstile_token=str(cleaned_data.get("turnstile_token", "")),
        )


class ContactService:
    """Verify a request and hand it directly to Resend, with no persistence."""

    def __init__(self, http_client: HTTPClient | None = None) -> None:
        self._http_client = http_client

    def submit(self, message: ContactMessage, *, remote_ip: str = "") -> None:
        self.verify_turnstile(message.turnstile_token, remote_ip=remote_ip)
        self.deliver(message)

    def verify_turnstile(self, token: str, *, remote_ip: str = "") -> None:
        if not settings.CONTACT_TURNSTILE_ENABLED:
            return

        secret = settings.CONTACT_TURNSTILE_SECRET_KEY
        if not secret or not token:
            raise ContactVerificationError("Contact verification failed.")

        payload = {"secret": secret, "response": token}
        if remote_ip:
            payload["remoteip"] = remote_ip

        try:
            response = self._post(
                TURNSTILE_VERIFY_URL,
                data=payload,
                timeout=settings.CONTACT_TURNSTILE_TIMEOUT_SECONDS,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise ContactVerificationError("Contact verification failed.")
            result = response.json()
        except ContactVerificationError:
            raise
        except (httpx.RequestError, ValueError, TypeError) as exc:
            raise ContactVerificationError("Contact verification failed.") from exc

        if not isinstance(result, dict) or result.get("success") is not True:
            raise ContactVerificationError("Contact verification failed.")

        allowed_hostnames = set(settings.CONTACT_TURNSTILE_HOSTNAMES)
        if allowed_hostnames and result.get("hostname") not in allowed_hostnames:
            raise ContactVerificationError("Contact verification failed.")
        if result.get("action") != settings.CONTACT_TURNSTILE_ACTION:
            raise ContactVerificationError("Contact verification failed.")

    def deliver(self, message: ContactMessage) -> None:
        api_key = settings.CONTACT_RESEND_API_KEY
        recipient = settings.CONTACT_TO_EMAIL
        if not api_key or not recipient:
            raise ContactDeliveryError("Contact delivery is unavailable.")

        payload = {
            "from": settings.CONTACT_FROM_EMAIL,
            "to": [recipient],
            "reply_to": message.email,
            "subject": f"[1415 Agri] {message.subject}",
            "text": message.message,
        }
        try:
            response = self._post(
                RESEND_EMAILS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=settings.CONTACT_RESEND_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as exc:
            raise ContactDeliveryError("Contact delivery failed.") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise ContactDeliveryError("Contact delivery failed.")

    def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        if self._http_client is not None:
            return self._http_client.post(url, **kwargs)
        timeout = kwargs.pop("timeout")
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, **kwargs)
