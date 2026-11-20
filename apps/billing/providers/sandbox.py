"""A stand-in payment provider for local development.

It behaves like a real one where it matters: checkout happens on a separate page,
and the only thing that changes a subscription is a signed webhook. No money moves.
"""
import hashlib
import hmac
import json
import secrets
from datetime import date

from django.conf import settings
from django.urls import reverse

from .base import EVENT_TYPES, ProviderEvent, WebhookError

SIGNATURE_HEADER = "X-Sandbox-Signature"


class SandboxProvider:
    name = "sandbox"

    def _secret(self) -> bytes:
        secret = settings.CASSILION["SANDBOX_WEBHOOK_SECRET"]
        if not secret:
            raise WebhookError("The sandbox provider is not configured.")
        return secret.encode()

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret(), body, hashlib.sha256).hexdigest()

    def create_checkout(self, order, request) -> str:
        return reverse("billing:sandbox_checkout", args=[order.ref])

    def build_event(self, order, event_type: str, period_end: date | None = None) -> tuple[bytes, dict]:
        """What the provider would send us. Used by the sandbox checkout page and by tests."""
        payload = {
            "id": "evt_" + secrets.token_urlsafe(10),
            "type": event_type,
            "order_ref": order.ref,
            "subscription_ref": f"sub_sandbox_{order.subscription_id}",
            "period_end": period_end.isoformat() if period_end else None,
        }
        body = json.dumps(payload, sort_keys=True).encode()
        return body, {SIGNATURE_HEADER: self.sign(body)}

    def parse_webhook(self, body: bytes, headers) -> ProviderEvent:
        signature = headers.get(SIGNATURE_HEADER) or headers.get(SIGNATURE_HEADER.lower()) or ""
        if not hmac.compare_digest(signature, self.sign(body)):
            raise WebhookError("Signature does not match.")
        try:
            payload = json.loads(body)
            event_type = payload["type"]
            if event_type not in EVENT_TYPES:
                raise WebhookError(f"Unknown event type: {event_type}")
            return ProviderEvent(
                event_id=str(payload["id"]),
                type=event_type,
                order_ref=str(payload["order_ref"]),
                subscription_ref=str(payload.get("subscription_ref") or ""),
                period_end=date.fromisoformat(payload["period_end"]) if payload.get("period_end") else None,
                raw=payload,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise WebhookError(f"Malformed event: {exc}") from exc
