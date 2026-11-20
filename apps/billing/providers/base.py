"""The seam for payment methods.

Every method implements :class:`PaymentProvider`. The shop only talks to this interface, so a
method can be switched on or off in the backoffice without touching orders or invoices.

A signed provider message (webhook), or asking the provider server to server when the customer
comes back, is the only source of truth for "paid". The browser never tells us.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

# What the sandbox sends; real providers have their own names, translated in their modules.
EVENT_PAID = "payment.succeeded"
EVENT_FAILED = "payment.failed"
EVENT_TYPES = (EVENT_PAID, EVENT_FAILED)


@dataclass
class StartResult:
    """Where to send the customer next."""

    redirect_url: str = ""
    instructions: dict = field(default_factory=dict)  # e.g. bank details


@dataclass
class WebhookEvent:
    """A provider message in our terms. ``kind`` is one of: payment_succeeded, payment_failed,
    payment_processing (accepted, money not there yet), approved (PayPal: take the money now),
    refund (made at the provider - by us or in its dashboard), dispute_opened, dispute_won,
    dispute_lost, ignored."""

    kind: str
    provider_id: str = ""  # the payment at the provider
    order_ref: str = ""
    amount: Decimal | None = None
    raw: dict = field(default_factory=dict)
    event_id: str = ""  # the provider's id of this message, for de-duplication
    provider_type: str = ""  # the provider's own name for the event
    occurred_at: datetime | None = None
    refund_id: str = ""
    reason: str = ""
    # The provider's payment may be known under a second id (Stripe: checkout session and
    # payment intent; PayPal: order and capture); ``rename_to`` becomes the stored id.
    alt_provider_id: str = ""
    rename_to: str = ""


@dataclass
class RefundResult:
    provider_id: str


class WebhookRejected(Exception):
    """Bad signature or malformed payload. Answer 400 and change nothing."""


WebhookError = WebhookRejected  # the name the first version used


class PaymentProvider:
    code: ClassVar[str]
    label: ClassVar[str]

    def start(self, order, request) -> StartResult:  # pragma: no cover
        raise NotImplementedError

    def parse_webhook(self, body: bytes, headers, config) -> WebhookEvent:  # pragma: no cover
        raise NotImplementedError

    def refund(self, payment, amount: Decimal) -> RefundResult:  # pragma: no cover
        raise NotImplementedError

    def confirm(self, payment) -> WebhookEvent | None:
        """Ask the provider about a payment the customer just came back from (server to
        server, so as trustworthy as a webhook). ``None``: nothing to report yet."""
        return None

    def ledger(self, config, since) -> list[tuple[str, Decimal, str]] | None:
        """``(provider id, amount, order ref)`` of collected payments since ``since``, for the
        nightly reconciliation. ``None``: this provider cannot list them."""
        return None
