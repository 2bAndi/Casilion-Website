"""Write the payment history: every status change goes through :func:`set_status`,
everything else that happened through :func:`record`. Both append; nothing is ever changed."""

from __future__ import annotations

from decimal import Decimal

from apps.billing.models import PaymentEvent

KIND_LABELS = {
    "created": "Payment started",
    "payment_succeeded": "Payment received",
    "payment_failed": "Payment failed",
    "payment_processing": "Payment on its way (e.g. direct debit)",
    "amount_mismatch": "Amount differs",
    "duplicate": "Same message again",
    "marked_paid": "Marked as paid by the team",
    "bank_statement": "Found in the bank statement",
    "cancelled": "Payment cancelled",
    "expired": "Not paid in time",
    "refund": "Refund by the team",
    "refund_external": "Refund made at the provider",
    "refund_confirmed": "Provider confirmed our refund",
    "late_refund": "Late payment paid back",
    "dispute_opened": "Chargeback / dispute opened",
    "dispute_won": "Dispute won",
    "dispute_lost": "Dispute lost – money charged back",
    "ignored": "Message without effect",
    "rejected": "Message rejected",
}


def label(kind: str) -> str:
    return str(KIND_LABELS.get(kind, kind))


def record(
    payment=None,
    *,
    kind: str,
    source: str,
    order=None,
    actor=None,
    status_from: str = "",
    status_to: str = "",
    amount: Decimal | None = None,
    provider: str = "",
    provider_event_id: str = "",
    provider_type: str = "",
    outcome: str = "",
    note: str = "",
    raw: dict | None = None,
    occurred_at=None,
) -> PaymentEvent:
    order = order or (payment.order if payment is not None else None)
    event = PaymentEvent(
        payment=payment,
        order=order,
        source=source,
        kind=kind[:30],
        provider=provider or (payment.provider if payment is not None else ""),
        provider_event_id=provider_event_id[:120],
        provider_type=provider_type[:80],
        amount=amount,
        status_from=status_from,
        status_to=status_to,
        outcome=outcome[:120],
        note=note[:300],
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        raw=raw or {},
        occurred_at=occurred_at,
    )
    event.save()
    return event


def set_status(payment, status: str, *, kind: str, source: str, **details) -> bool:
    """Change the status of a payment and write why. Returns False if it already had it."""
    old = payment.status
    if old == status:
        record(payment, kind=kind, source=source, status_from=old, status_to=old, **details)
        return False
    payment.status = status
    payment.save(update_fields=["status", "updated_at"])
    record(payment, kind=kind, source=source, status_from=old, status_to=status, **details)
    return True


def created(
    payment, *, source: str = "system", kind: str = "created", actor=None, occurred_at=None
) -> None:
    record(
        payment,
        kind=kind,
        source=source,
        status_to=payment.status,
        amount=payment.amount,
        actor=actor,
        occurred_at=occurred_at,
    )
