"""Stripe: cards (with Apple Pay / Google Pay) on Stripe's hosted Checkout page.

Taken over from the Tangosummer ticket shop. Plain REST over HTTPS (form-encoded, like Stripe's
own libraries send it); no SDK. The backoffice holds a secret key (``sk_test_…`` or
``sk_live_…``; a restricted ``rk_…`` key with write access to Checkout Sessions and Refunds
works too) and the webhook signing secret (``whsec_…``) of an endpoint pointing at
``/billing/webhook/stripe/`` (``set_up_webhook`` creates it from the settings page; a restricted
key then also needs write access to webhook endpoints) with these events:

    checkout.session.completed, checkout.session.async_payment_succeeded,
    checkout.session.async_payment_failed, checkout.session.expired,
    refund.created, charge.dispute.created, charge.dispute.closed

A payment starts with the session id (``cs_…``); once paid it is stored under its payment
intent (``pi_…``), which refunds and disputes refer to.

Each term of a plan is one payment (``mode=payment``). Stripe subscriptions are not used: a
renewal is a new order with its own invoice and pay link.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from decimal import Decimal

from django.urls import reverse

from apps.billing import events
from apps.billing.models import Payment, PaymentStatus

from .base import PaymentProvider, RefundResult, StartResult, WebhookEvent, WebhookRejected
from .http import ProviderError, check, client

API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE = 300  # seconds

# What our endpoint subscribes to (also set up by :func:`set_up_webhook`).
WEBHOOK_EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
    "refund.created",
    "charge.dispute.created",
    "charge.dispute.closed",
)

KINDS = {
    "checkout.session.async_payment_succeeded": "payment_succeeded",
    "checkout.session.async_payment_failed": "payment_failed",
    "checkout.session.expired": "payment_failed",
    "refund.created": "refund",
    "charge.dispute.created": "dispute_opened",
    "charge.dispute.closed": "dispute_closed",
}


def cents(amount: Decimal) -> int:
    return int((Decimal(amount) * 100).quantize(Decimal("1")))


def euros(value) -> Decimal | None:
    return None if value in (None, "") else (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def mode(secret_key: str) -> str:
    """``test`` or ``live`` from the key itself."""
    return "live" if "_live_" in (secret_key or "") else "test"


def _config():
    from . import config

    return config()


def _headers(config, idempotency_key: str = "") -> dict:
    headers = {"Authorization": f"Bearer {config.stripe_secret_key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def verify(body: bytes, header: str, secret: str, now: float | None = None) -> None:
    """Stripe's ``Stripe-Signature``: ``t=<unix time>,v1=<hex HMAC-SHA256 of "t.body">``."""
    if not secret:
        raise WebhookRejected("no webhook secret configured")
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        key, _sep, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError) as exc:
        raise WebhookRejected("bad signature header") from exc
    if abs((now or time.time()) - timestamp) > SIGNATURE_TOLERANCE:
        raise WebhookRejected("signature too old")
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, given) for given in parts.get("v1", [])):
        raise WebhookRejected("bad signature")


def session_event(session: dict, event_id: str = "", event_type: str = "") -> WebhookEvent:
    """A checkout session in our terms (from a webhook or from asking Stripe directly)."""
    paid = session.get("payment_status") in ("paid", "no_payment_required")
    if event_type in KINDS:
        kind = KINDS[event_type]
    elif session.get("status") == "complete":
        kind = "payment_succeeded" if paid else "payment_processing"
    elif session.get("status") == "expired":
        kind = "payment_failed"
    else:
        kind = "ignored"
    intent = session.get("payment_intent") or ""
    if isinstance(intent, dict):
        intent = intent.get("id", "")
    return WebhookEvent(
        kind=kind,
        provider_id=session.get("id", ""),
        alt_provider_id=intent,
        rename_to=intent if kind == "payment_succeeded" else "",
        order_ref=session.get("client_reference_id") or "",
        amount=euros(session.get("amount_total")),
        raw={"type": event_type or "checkout.session", "session": session},
        event_id=event_id,
        provider_type=event_type or "checkout.session.retrieved",
    )


class StripeCard(PaymentProvider):
    code = "stripe"
    label = "Card, Apple Pay, Google Pay"
    method_types = ("card",)

    def start(self, order, request) -> StartResult:
        config = _config()
        payment = Payment.objects.create(order=order, provider="stripe", amount=order.total, status=PaymentStatus.PENDING)
        detail = request.build_absolute_uri(reverse("billing:order", args=[order.ref]))
        back = request.build_absolute_uri(reverse("billing:return", args=[payment.pk]))
        line = order.first_line
        form = {
            "mode": "payment",
            "client_reference_id": order.ref,
            "customer_email": order.email,
            "locale": "de" if order.language.startswith("de") else "en",
            "success_url": back,
            "cancel_url": detail,
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": "eur",
            "line_items[0][price_data][unit_amount]": cents(order.total),
            "line_items[0][price_data][product_data][name]": f"{line.description if line else 'Order'} · {order.ref}"[:250],
            "metadata[order]": order.ref,
            "payment_intent_data[description]": f"Cassilion {order.ref}",
            "payment_intent_data[metadata][order]": order.ref,
        }
        for n, kind in enumerate(self.method_types):
            form[f"payment_method_types[{n}]"] = kind
        with client() as http:
            session = check(
                http.post(f"{API}/checkout/sessions", data=form, headers=_headers(config, f"checkout-{payment.pk}")),
                "Stripe",
            )
        payment.provider_id = session["id"]
        payment.raw = {"session": session.get("id"), "methods": list(self.method_types)}
        payment.save(update_fields=["provider_id", "raw", "updated_at"])
        events.created(payment, source="customer")
        return StartResult(redirect_url=session["url"])

    def parse_webhook(self, body: bytes, headers, config) -> WebhookEvent:
        import json

        verify(body, headers.get("Stripe-Signature", ""), config.stripe_webhook_secret)
        try:
            event = json.loads(body)
            kind_type, obj = event["type"], event["data"]["object"]
        except (KeyError, ValueError, TypeError) as exc:
            raise WebhookRejected("malformed payload") from exc
        occurred = datetime.fromtimestamp(event.get("created") or 0, tz=UTC)
        if kind_type.startswith("checkout.session."):
            result = session_event(obj, event.get("id", ""), kind_type)
            result.occurred_at = occurred
            return result
        kind = KINDS.get(kind_type, "ignored")
        intent = obj.get("payment_intent") or ""
        if kind == "dispute_closed":
            kind = "dispute_won" if obj.get("status") == "won" else "dispute_lost"
        return WebhookEvent(
            kind=kind,
            provider_id=intent if isinstance(intent, str) else intent.get("id", ""),
            amount=euros(obj.get("amount")),
            raw=event,
            event_id=event.get("id", ""),
            provider_type=kind_type,
            occurred_at=occurred,
            refund_id=obj.get("id", "") if kind == "refund" else "",
            reason=obj.get("reason") or "",
        )

    def confirm(self, payment) -> WebhookEvent | None:
        raw = payment.raw or {}
        session_id = raw.get("session") or payment.provider_id
        if not isinstance(session_id, str) or not session_id.startswith("cs_"):
            return None
        with client() as http:
            session = check(http.get(f"{API}/checkout/sessions/{session_id}", headers=_headers(_config())), "Stripe")
        result = session_event(session)
        result.event_id = f"session:{session_id}:{result.kind}"
        return None if result.kind == "ignored" else result

    def refund(self, payment, amount: Decimal) -> RefundResult:
        intent = payment.provider_id
        if not intent.startswith("pi_"):
            raise ProviderError("Stripe does not know this payment yet - refund it in the Stripe dashboard.")
        with client() as http:
            refund = check(
                http.post(
                    f"{API}/refunds",
                    data={"payment_intent": intent, "amount": cents(amount)},
                    headers=_headers(_config(), f"refund-{payment.pk}-{cents(amount)}-{payment.refunds.count()}"),
                ),
                "Stripe",
            )
        return RefundResult(provider_id=refund["id"])

    def ledger(self, config, since):
        rows, after = [], ""
        with client() as http:
            while True:
                params = {"created[gte]": int(since.timestamp()), "limit": 100}
                if after:
                    params["starting_after"] = after
                page = check(
                    http.get(f"{API}/payment_intents", params=params, headers=_headers(config)),
                    "Stripe",
                )
                for intent in page.get("data", []):
                    if intent.get("status") == "succeeded":
                        rows.append(
                            (
                                intent["id"],
                                euros(intent.get("amount_received")),
                                (intent.get("metadata") or {}).get("order", ""),
                            )
                        )
                if not page.get("has_more") or not page.get("data"):
                    return rows
                after = page["data"][-1]["id"]


def check_connection(config) -> str:
    """For the settings page: the account's name and mode, or :class:`ProviderError`."""
    with client() as http:
        account = check(http.get(f"{API}/account", headers=_headers(config)), "Stripe")
    name = (account.get("settings") or {}).get("dashboard", {}).get("display_name") or account.get(
        "id", ""
    )
    return f"{name} ({mode(config.stripe_secret_key)})"


def set_up_webhook(config, url: str) -> str:
    """Create our endpoint at Stripe and store its signing secret.

    Stripe shows the secret only when an endpoint is created, so an existing endpoint for the
    same address is replaced. Returns the endpoint id."""
    if not url.startswith("https://"):
        raise ProviderError("Webhooks need the https address of this site.")
    with client() as http:
        endpoints = check(
            http.get(
                f"{API}/webhook_endpoints", params={"limit": 100}, headers=_headers(config)
            ),
            "Stripe",
        )
        form = {"url": url, "description": "Cassilion"}
        for n, event in enumerate(WEBHOOK_EVENTS):
            form[f"enabled_events[{n}]"] = event
        endpoint = check(
            http.post(f"{API}/webhook_endpoints", data=form, headers=_headers(config)),
            "Stripe",
        )
        for old in endpoints.get("data", []):
            if old.get("url") == url and old.get("id") != endpoint.get("id"):
                check(
                    http.delete(
                        f"{API}/webhook_endpoints/{old['id']}", headers=_headers(config)
                    ),
                    "Stripe",
                )
    config.stripe_webhook_secret = endpoint["secret"]
    config.save(update_fields=["stripe_webhook_secret"])
    return endpoint["id"]
