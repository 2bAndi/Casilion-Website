"""PayPal Checkout (Orders API v2), plain REST over HTTPS. Taken over from the Tangosummer ticket shop.

Create a REST app in the PayPal developer dashboard (sandbox first, then live) and enter its
client id and secret in the backoffice, plus the id of a webhook pointing at ``/billing/webhook/paypal/``
(``set_up_webhook`` creates it from the settings page) with these events:

    CHECKOUT.ORDER.APPROVED, PAYMENT.CAPTURE.COMPLETED, PAYMENT.CAPTURE.PENDING,
    PAYMENT.CAPTURE.DENIED, PAYMENT.CAPTURE.REFUNDED, PAYMENT.CAPTURE.REVERSED,
    CUSTOMER.DISPUTE.CREATED, CUSTOMER.DISPUTE.RESOLVED

The customer approves on PayPal's page; we capture the money when they come back (or when the
approval webhook arrives, whichever is first). A payment starts with the PayPal order id and is
stored under the capture id once captured; refunds and disputes refer to the capture.
"""

from __future__ import annotations

import json
from decimal import Decimal
from urllib.parse import urlsplit

from django.core.cache import cache
from django.urls import reverse

from apps.billing import events
from apps.billing.models import Payment, PaymentStatus

from .base import PaymentProvider, RefundResult, StartResult, WebhookEvent, WebhookRejected
from .http import ProviderError, check, client

LIVE = "https://api-m.paypal.com"
SANDBOX = "https://api-m.sandbox.paypal.com"

KINDS = {
    "CHECKOUT.ORDER.APPROVED": "approved",
    "PAYMENT.CAPTURE.COMPLETED": "payment_succeeded",
    "PAYMENT.CAPTURE.PENDING": "payment_processing",
    "PAYMENT.CAPTURE.DENIED": "payment_failed",
    "PAYMENT.CAPTURE.DECLINED": "payment_failed",
    "PAYMENT.CAPTURE.REFUNDED": "refund",
    "PAYMENT.CAPTURE.REVERSED": "dispute_lost",
    "CUSTOMER.DISPUTE.CREATED": "dispute_opened",
    "CUSTOMER.DISPUTE.RESOLVED": "dispute_closed",
}
SIGNATURE_HEADERS = {
    "auth_algo": "PAYPAL-AUTH-ALGO",
    "cert_url": "PAYPAL-CERT-URL",
    "transmission_id": "PAYPAL-TRANSMISSION-ID",
    "transmission_sig": "PAYPAL-TRANSMISSION-SIG",
    "transmission_time": "PAYPAL-TRANSMISSION-TIME",
}


def _config():
    from . import config

    return config()


def base(config) -> str:
    return SANDBOX if config.paypal_sandbox else LIVE


def money(value) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value)).quantize(Decimal("0.01"))


def token(config) -> str:
    """OAuth access token, cached a little shorter than PayPal keeps it."""
    key = f"paypal-token:{config.paypal_client_id}:{config.paypal_sandbox}"
    cached = cache.get(key)
    if cached:
        return cached
    with client() as http:
        data = check(
            http.post(
                f"{base(config)}/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(config.paypal_client_id, config.paypal_secret),
            ),
            "PayPal",
        )
    cache.set(key, data["access_token"], max(int(data.get("expires_in", 3600)) - 300, 60))
    return data["access_token"]


def _headers(config, request_id: str = "") -> dict:
    headers = {"Authorization": f"Bearer {token(config)}", "Content-Type": "application/json"}
    if request_id:
        headers["PayPal-Request-Id"] = request_id
    return headers


def capture_event(order_data: dict, event_id: str = "") -> WebhookEvent | None:
    """A captured (or still pending) PayPal order in our terms."""
    units = order_data.get("purchase_units") or [{}]
    captures = ((units[0].get("payments") or {}).get("captures")) or []
    if not captures:
        return None
    capture = captures[0]
    status = capture.get("status")
    kind = {"COMPLETED": "payment_succeeded", "PENDING": "payment_processing"}.get(
        status, "payment_failed"
    )
    return WebhookEvent(
        kind=kind,
        provider_id=order_data.get("id", ""),
        alt_provider_id=capture.get("id", ""),
        rename_to=capture.get("id", "") if kind == "payment_succeeded" else "",
        order_ref=units[0].get("custom_id", ""),
        amount=money((capture.get("amount") or {}).get("value")),
        raw={"type": "order.captured", "order": order_data},
        event_id=event_id or f"capture:{capture.get('id', '')}:{status}",
        provider_type=f"CAPTURE.{status}",
    )


class PayPal(PaymentProvider):
    code = "paypal"
    label = "PayPal"

    def start(self, order, request) -> StartResult:
        config = _config()
        line = order.first_line
        payment = Payment.objects.create(
            order=order, provider="paypal", amount=order.total, status=PaymentStatus.PENDING
        )
        back = request.build_absolute_uri(reverse("billing:return", args=[payment.pk]))
        detail = request.build_absolute_uri(reverse("billing:order", args=[order.ref]))
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": order.ref,
                    "custom_id": order.ref,
                    "invoice_id": f"{order.ref}-{payment.pk}",
                    "description": f"{line.description if line else 'Order'} · {order.ref}"[:127],
                    "amount": {"currency_code": "EUR", "value": f"{order.total:.2f}"},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "Cassilion",
                        "locale": "de-DE" if order.language.startswith("de") else "en-DE",
                        "shipping_preference": "NO_SHIPPING",
                        "user_action": "PAY_NOW",
                        "return_url": back,
                        "cancel_url": detail,
                    }
                }
            },
        }
        with client() as http:
            created = check(
                http.post(
                    f"{base(config)}/v2/checkout/orders",
                    content=json.dumps(body),
                    headers=_headers(config, f"order-{payment.pk}"),
                ),
                "PayPal",
            )
        link = next(
            (
                x["href"]
                for x in created.get("links", [])
                if x.get("rel") in ("payer-action", "approve")
            ),
            "",
        )
        if not link:
            raise ProviderError("PayPal: no approval link in the answer")
        payment.provider_id = created["id"]
        payment.raw = {"paypal_order": created["id"]}
        payment.save(update_fields=["provider_id", "raw", "updated_at"])
        events.created(payment, source="customer")
        return StartResult(redirect_url=link)

    def capture(self, payment) -> WebhookEvent | None:
        """Take the money of an approved PayPal order (idempotent at PayPal)."""
        config = _config()
        order_id = (payment.raw or {}).get("paypal_order") or payment.provider_id
        with client() as http:
            response = http.post(
                f"{base(config)}/v2/checkout/orders/{order_id}/capture",
                content="{}",
                headers=_headers(config, f"capture-{payment.pk}"),
            )
            if response.status_code == 422:  # already captured, or not approved (yet)
                response = http.get(
                    f"{base(config)}/v2/checkout/orders/{order_id}",
                    headers=_headers(config),
                )
            data = check(response, "PayPal")
        return capture_event(data)

    confirm = capture

    def parse_webhook(self, body: bytes, headers, config) -> WebhookEvent:
        try:
            event = json.loads(body)
            event_type, resource = event["event_type"], event["resource"]
        except (KeyError, ValueError, TypeError) as exc:
            raise WebhookRejected("malformed payload") from exc
        if not config.paypal_webhook_id:
            raise WebhookRejected("no webhook id configured")
        check_body = {key: headers.get(name, "") for key, name in SIGNATURE_HEADERS.items()}
        # Cheap checks first: anyone can post here, and each real check is a call to PayPal.
        cert_host = urlsplit(check_body["cert_url"]).hostname or ""
        if not all(check_body.values()) or not (
            cert_host == "paypal.com" or cert_host.endswith(".paypal.com")
        ):
            raise WebhookRejected("missing or foreign signature headers")
        check_body.update(webhook_id=config.paypal_webhook_id, webhook_event=event)
        try:
            with client() as http:
                verdict = check(
                    http.post(
                        f"{base(config)}/v1/notifications/verify-webhook-signature",
                        content=json.dumps(check_body),
                        headers=_headers(config),
                    ),
                    "PayPal",
                )
        except ProviderError as exc:
            raise WebhookRejected(str(exc)) from exc
        if verdict.get("verification_status") != "SUCCESS":
            raise WebhookRejected("bad signature")

        kind = KINDS.get(event_type, "ignored")
        common = {"raw": event, "event_id": event.get("id", ""), "provider_type": event_type}
        if kind == "approved":
            return WebhookEvent(kind=kind, provider_id=resource.get("id", ""), **common)
        if event_type.startswith("PAYMENT.CAPTURE.") and kind != "refund":
            related = ((resource.get("supplementary_data") or {}).get("related_ids")) or {}
            return WebhookEvent(
                kind=kind,
                provider_id=resource.get("id", ""),
                alt_provider_id=related.get("order_id", ""),
                rename_to=resource.get("id", "") if kind == "payment_succeeded" else "",
                order_ref=resource.get("custom_id", ""),
                amount=money((resource.get("amount") or {}).get("value")),
                **common,
            )
        if kind == "refund":
            up = next((x["href"] for x in resource.get("links", []) if x.get("rel") == "up"), "")
            return WebhookEvent(
                kind=kind,
                provider_id=up.rstrip("/").rsplit("/", 1)[-1],
                amount=money((resource.get("amount") or {}).get("value")),
                refund_id=resource.get("id", ""),
                reason=resource.get("note_to_payer", ""),
                **common,
            )
        if event_type.startswith("CUSTOMER.DISPUTE."):
            disputed = (resource.get("disputed_transactions") or [{}])[0]
            if kind == "dispute_closed":
                outcome = (resource.get("dispute_outcome") or {}).get("outcome_code", "")
                kind = "dispute_won" if outcome == "RESOLVED_SELLER_FAVOUR" else "dispute_lost"
            return WebhookEvent(
                kind=kind,
                provider_id=disputed.get("seller_transaction_id", ""),
                amount=money((resource.get("dispute_amount") or {}).get("value")),
                reason=resource.get("reason", ""),
                **common,
            )
        return WebhookEvent(kind="ignored", **common)

    def refund(self, payment, amount: Decimal) -> RefundResult:
        capture_id = payment.provider_id
        if not capture_id:
            raise ProviderError("PayPal does not know this payment - refund it at PayPal.")
        config = _config()
        with client() as http:
            refund = check(
                http.post(
                    f"{base(config)}/v2/payments/captures/{capture_id}/refund",
                    content=json.dumps(
                        {"amount": {"value": f"{amount:.2f}", "currency_code": "EUR"}}
                    ),
                    headers=_headers(
                        config, f"refund-{payment.pk}-{amount}-{payment.refunds.count()}"
                    ),
                ),
                "PayPal",
            )
        return RefundResult(provider_id=refund["id"])

    def ledger(self, config, since):
        """Needs the "Transaction search" permission of the PayPal app; ``None`` without it."""
        from django.utils import timezone

        params = {
            "start_date": since.strftime("%Y-%m-%dT%H:%M:%S-0000"),
            "end_date": timezone.now().strftime("%Y-%m-%dT%H:%M:%S-0000"),
            "fields": "transaction_info",
            "page_size": 500,
        }
        try:
            with client() as http:
                data = check(
                    http.get(
                        f"{base(config)}/v1/reporting/transactions",
                        params=params,
                        headers=_headers(config),
                    ),
                    "PayPal",
                )
        except ProviderError:
            return None
        rows = []
        for item in data.get("transaction_details", []):
            info = item.get("transaction_info") or {}
            value = money((info.get("transaction_amount") or {}).get("value"))
            if info.get("transaction_status") == "S" and value and value > 0:
                rows.append((info.get("transaction_id", ""), value, info.get("custom_field", "")))
        return rows


def set_up_webhook(config, url: str) -> str:
    """Create (or bring up to date) the webhook of the config's PayPal app for ``url`` and
    store its id, which every incoming message is verified against. Returns the id."""
    if not url.startswith("https://"):
        raise ProviderError("Webhooks need the https address of this site.")
    events = [{"name": name} for name in KINDS]
    with client() as http:
        hooks = check(
            http.get(f"{base(config)}/v1/notifications/webhooks", headers=_headers(config)),
            "PayPal",
        )
        mine = next((h for h in hooks.get("webhooks", []) if h.get("url") == url), None)
        if mine:
            check(
                http.patch(
                    f"{base(config)}/v1/notifications/webhooks/{mine['id']}",
                    content=json.dumps(
                        [{"op": "replace", "path": "/event_types", "value": events}]
                    ),
                    headers=_headers(config),
                ),
                "PayPal",
            )
            webhook_id = mine["id"]
        else:
            created = check(
                http.post(
                    f"{base(config)}/v1/notifications/webhooks",
                    content=json.dumps({"url": url, "event_types": events}),
                    headers=_headers(config),
                ),
                "PayPal",
            )
            webhook_id = created["id"]
    config.paypal_webhook_id = webhook_id
    config.save(update_fields=["paypal_webhook_id"])
    return webhook_id


def check_connection(config) -> str:
    token(config)
    return "sandbox" if config.paypal_sandbox else "live"
