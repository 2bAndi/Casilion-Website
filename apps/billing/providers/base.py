"""The seam for the payment provider (decision D1 is still open).

The rest of the code only knows these two calls and these four event types. A
merchant of record or a payment processor is one more module next to sandbox.py.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

EVENT_PAID = "payment.succeeded"
EVENT_FAILED = "payment.failed"
EVENT_PAST_DUE = "subscription.past_due"
EVENT_CANCELLED = "subscription.cancelled"
EVENT_TYPES = (EVENT_PAID, EVENT_FAILED, EVENT_PAST_DUE, EVENT_CANCELLED)


class WebhookError(Exception):
    """The webhook is not authentic or not understood. Answer 400 and change nothing."""


@dataclass
class ProviderEvent:
    event_id: str
    type: str
    order_ref: str
    subscription_ref: str = ""
    period_end: date | None = None
    raw: dict = field(default_factory=dict)


class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, order, request) -> str:
        """Return the URL where the customer pays for this order."""

    def parse_webhook(self, body: bytes, headers) -> ProviderEvent:
        """Verify the signature and translate the provider's event. Raise WebhookError otherwise."""
