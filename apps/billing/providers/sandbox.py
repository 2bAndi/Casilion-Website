"""A stand-in payment provider for local development and staging.

It behaves like a real one where it matters: checkout happens on a separate page, and the
only thing that changes an order is a signed webhook. No money moves.
"""
import hashlib
import hmac
import json
import secrets
from decimal import Decimal

from django.conf import settings
from django.urls import reverse

from apps.billing import events
from apps.billing.models import Payment, PaymentStatus

from .base import EVENT_FAILED, EVENT_PAID, EVENT_TYPES, PaymentProvider, RefundResult, StartResult, WebhookEvent, WebhookRejected

SIGNATURE_HEADER = "X-Sandbox-Signature"
KINDS = {EVENT_PAID: "payment_succeeded", EVENT_FAILED: "payment_failed"}


class SandboxProvider(PaymentProvider):
    code = "sandbox"
    name = "sandbox"
    label = "Sandbox (no money moves)"

    def _secret(self) -> bytes:
        secret = settings.CASSILION["SANDBOX_WEBHOOK_SECRET"]
        if not secret or not settings.CASSILION["SANDBOX_PAYMENTS"]:
            raise WebhookRejected("The sandbox provider is not switched on.")
        return secret.encode()

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret(), body, hashlib.sha256).hexdigest()

    def _payment(self, order) -> Payment:
        payment = order.payments.filter(provider=self.code, status=PaymentStatus.PENDING).last()
        if payment is None:
            payment = Payment.objects.create(order=order, provider=self.code, amount=order.total)
            payment.provider_id = f"sbx_{payment.pk}_{secrets.token_hex(4)}"
            payment.save(update_fields=["provider_id", "updated_at"])
            events.created(payment, source="customer")
        return payment

    def start(self, order, request) -> StartResult:
        self._payment(order)
        return StartResult(redirect_url=reverse("billing:sandbox_checkout", args=[order.ref]))

    def build_event(self, order, event_type: str, amount: Decimal | None = None) -> tuple[bytes, dict]:
        """What the provider would send us. Used by the sandbox checkout page and by tests."""
        payment = self._payment(order)
        payload = {
            "id": "evt_" + secrets.token_urlsafe(10),
            "type": event_type,
            "order_ref": order.ref,
            "payment": payment.provider_id,
            "amount": str(payment.amount if amount is None else amount),
        }
        body = json.dumps(payload, sort_keys=True).encode()
        return body, {SIGNATURE_HEADER: self.sign(body)}

    def parse_webhook(self, body: bytes, headers, config=None) -> WebhookEvent:
        signature = headers.get(SIGNATURE_HEADER) or headers.get(SIGNATURE_HEADER.lower()) or ""
        if not hmac.compare_digest(signature, self.sign(body)):
            raise WebhookRejected("Signature does not match.")
        try:
            payload = json.loads(body)
            event_type = payload["type"]
            if event_type not in EVENT_TYPES:
                raise WebhookRejected(f"Unknown event type: {event_type}")
            return WebhookEvent(
                kind=KINDS[event_type],
                provider_id=str(payload["payment"]),
                order_ref=str(payload["order_ref"]),
                amount=Decimal(str(payload["amount"])),
                raw=payload,
                event_id=str(payload["id"]),
                provider_type=event_type,
            )
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise WebhookRejected(f"Malformed event: {exc}") from exc

    def refund(self, payment, amount: Decimal) -> RefundResult:
        return RefundResult(provider_id="sbx_re_" + secrets.token_hex(6))
