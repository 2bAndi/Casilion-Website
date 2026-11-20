"""What happens when money moves: provider messages, paid orders, refunds, bank statements,
and the nightly comparison with the providers' own records.

Taken over from the Tangosummer ticket shop and cut to what a license shop needs.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as safe_xml
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.log import audit
from apps.licensing import services as licensing

from . import alerts, events, mails
from .models import COLLECTED, OPEN, Order, Payment, PaymentEvent, PaymentStatus, ReconciliationIssue, Refund, Subscription
from .providers import ProviderError, WebhookEvent, WebhookRejected, config, get_provider, is_configured

log = logging.getLogger(__name__)
TERM = timedelta(days=365)


class RefundError(Exception):
    pass


# --------------------------------------------------------------------------- provider messages


def process_webhook(provider_code: str, body: bytes, headers) -> str:
    """Verify and apply one provider message. Safe to call any number of times per event.

    Every message is written to the payment history - also rejected and ignored ones - and
    each provider event id is applied only once. Returns a short outcome string.
    """
    provider = get_provider(provider_code)
    try:
        event: WebhookEvent = provider.parse_webhook(body, headers, config())
    except NotImplementedError:
        raise WebhookRejected("This payment method has no webhook.") from None
    except WebhookRejected as exc:
        _record_rejected(provider_code, str(exc), body)
        alerts.webhook_rejected(provider_code, str(exc))
        raise
    return apply_event(provider_code, event)


def apply_event(provider_code: str, event: WebhookEvent, source: str = PaymentEvent.Source.WEBHOOK) -> str:
    """Apply a verified provider message: from a signed webhook, or from asking the provider
    directly when the customer comes back (``source`` = customer)."""
    details = {
        "source": source,
        "provider": provider_code,
        "provider_event_id": event.event_id,
        "provider_type": event.provider_type,
        "amount": event.amount,
        "raw": event.raw,
        "occurred_at": event.occurred_at,
    }
    if event.event_id and _seen(provider_code, event.event_id):
        return "duplicate"
    if event.kind == "ignored":
        PaymentEvent(kind="ignored", outcome="ignored", **details).save()
        return "ignored"
    try:
        with transaction.atomic():
            return _apply(provider_code, event, details)
    except WebhookRejected as exc:
        PaymentEvent(kind=event.kind, outcome=f"rejected: {exc}"[:120], **details).save()
        flag(ReconciliationIssue.Kind.UNKNOWN, provider_code, event.provider_id or event.event_id, f"{event.provider_type} {event.amount or ''}")
        raise


def _seen(provider_code: str, event_id: str) -> bool:
    """Each provider message is applied once, whichever way it arrived (webhook or return)."""
    return PaymentEvent.objects.filter(provider=provider_code, provider_event_id=event_id).exists()


def _record_rejected(provider_code: str, reason: str, body: bytes) -> None:
    # Anyone can post to the webhook address: keep at most 50 rejected messages per hour and provider.
    key = f"hook-rejected:{provider_code}"
    cache.add(key, 0, 3600)
    try:
        if cache.incr(key) > 50:
            return
    except ValueError:
        pass
    PaymentEvent(
        source=PaymentEvent.Source.WEBHOOK,
        kind="rejected",
        provider=provider_code,
        outcome=reason[:120],
        raw={"body": body[:5000].decode("utf-8", errors="replace")},
    ).save()


def flag(kind: str, provider: str, provider_id: str, detail: str, order=None) -> bool:
    """Put something on the list of money matters a person has to look at. One open entry per case."""
    _issue, created = ReconciliationIssue.objects.get_or_create(
        kind=kind,
        provider=provider[:20],
        provider_id=provider_id[:100],
        resolved=False,
        defaults={"detail": detail[:300], "order": order},
    )
    return created


def _apply(provider_code: str, event: WebhookEvent, details: dict) -> str:
    ids = [i for i in (event.provider_id, event.alt_provider_id) if i]
    payment = (
        Payment.objects.select_for_update(of=("self",))
        .select_related("order")
        .filter(Q(provider_id__in=ids) | Q(provider_ref__in=ids), provider=provider_code)
        .first()
        if ids
        else None
    )
    if payment is None:
        raise WebhookRejected("unknown payment")
    order, kind = payment.order, event.kind

    if kind == "approved":  # PayPal: the customer said yes, now take the money
        captured = get_provider(provider_code).confirm(payment)
        events.record(payment, kind=kind, outcome="capturing", **details)
        if captured is None:
            return "approved"
        if _seen(provider_code, captured.event_id):  # the customer's return got there first
            return "duplicate"
        follow = {**details, "raw": captured.raw, "provider_event_id": captured.event_id, "provider_type": captured.provider_type}
        return _apply(provider_code, captured, follow)

    if kind == "payment_failed":
        if payment.status in OPEN:
            events.set_status(payment, PaymentStatus.FAILED, kind=kind, outcome="failed", **details)
            payment_failed(order)
            return "failed"
        events.record(payment, kind=kind, outcome=f"no effect ({payment.status})", **details)
        return "ignored"

    if kind == "payment_processing":
        if payment.status == PaymentStatus.PENDING:
            events.set_status(payment, PaymentStatus.PROCESSING, kind=kind, outcome="processing", **details)
            return "processing"
        events.record(payment, kind=kind, outcome=f"no effect ({payment.status})", **details)
        return "ignored"

    if kind == "payment_succeeded":
        if payment.status in COLLECTED:
            events.record(payment, kind="duplicate", outcome="already paid", **details)
            return "duplicate"
        if event.amount is not None and event.amount != payment.amount:
            payment.raw = {**event.raw, "mismatch": True}
            payment.save(update_fields=["raw", "updated_at"])
            events.record(payment, kind="amount_mismatch", outcome="amount_mismatch", **details)
            detail = f"expected {payment.amount}, got {event.amount}"
            flag(ReconciliationIssue.Kind.AMOUNT, provider_code, payment.provider_id, detail, order)
            log.error("amount mismatch on %s: %s", payment, detail)
            alerts.alert("amount_mismatch", order=order, provider=provider_code, detail=detail)
            return "amount_mismatch"
        payment.raw = {**(payment.raw or {}), **event.raw}
        if event.rename_to and event.rename_to != payment.provider_id:
            payment.provider_ref, payment.provider_id = payment.provider_id, event.rename_to
        payment.save(update_fields=["raw", "provider_id", "provider_ref", "updated_at"])
        events.set_status(payment, PaymentStatus.SUCCEEDED, kind=kind, outcome="paid", **details)
        mark_paid(order, payment)
        return "paid"

    if kind == "refund":
        if event.refund_id and Refund.objects.filter(payment=payment, provider_id=event.refund_id).exists():
            events.record(payment, kind="refund_confirmed", outcome="ours", **details)
            return "refund_confirmed"
        amount = event.amount if event.amount is not None else payment.amount
        record_external_refund(payment, amount, provider_id=event.refund_id or event.event_id, reason=event.reason or "refunded at the provider", details=details)
        flag(ReconciliationIssue.Kind.EXTERNAL_REFUND, provider_code, payment.provider_id, f"{order.ref}: {amount} {event.reason}", order)
        return "refund_recorded"

    if kind == "dispute_opened":
        events.set_status(payment, PaymentStatus.DISPUTED, kind=kind, outcome="disputed", note=event.reason, **details)
        detail = f"{event.amount or payment.amount} {event.reason}".strip()
        flag(ReconciliationIssue.Kind.DISPUTE, provider_code, payment.provider_id, f"{order.ref}: {detail}", order)
        alerts.alert("dispute_opened", order=order, provider=provider_code, detail=detail)
        return "disputed"

    if kind == "dispute_won":
        back = PaymentStatus.PARTIALLY_REFUNDED if order.refunded_total else PaymentStatus.SUCCEEDED
        events.set_status(payment, back, kind=kind, outcome="won", **details)
        return "dispute_won"

    if kind == "dispute_lost":
        events.set_status(payment, PaymentStatus.CHARGED_BACK, kind=kind, outcome="charged back", **details)
        flag(ReconciliationIssue.Kind.DISPUTE, provider_code, payment.provider_id, f"{order.ref}: lost {event.amount or payment.amount}", order)
        alerts.alert("dispute_lost", order=order, provider=provider_code, detail=f"{event.amount or payment.amount}")
        return "charged_back"

    events.record(payment, kind=kind, outcome="unknown kind", **details)
    return "ignored"


# --------------------------------------------------------------------------- paid, failed


def mark_paid(order: Order, payment: Payment | None = None) -> Order:
    """Idempotent: the order becomes paid, every plan on it starts (or continues) its term, the
    license files are issued, the invoice is written and the confirmation goes out."""
    from apps.invoicing import services as invoicing

    order = Order.objects.select_for_update(of=("self",)).select_related("account", "placed_by").get(pk=order.pk)
    if order.status in Order.SETTLED:
        return order
    if order.status == Order.Status.CANCELLED:
        # The order was given up (not paid in time, or the plan was cancelled). A person decides
        # whether the money goes back or the plan starts after all.
        ref = payment.provider_id if payment else order.ref
        if flag(ReconciliationIssue.Kind.LATE_PAYMENT, payment.provider if payment else "", ref, f"{order.ref}: {order.total}", order):
            alerts.alert("late_payment", order=order, provider=payment.provider if payment else "", detail=str(order.total))
        return order

    today, account = timezone.localdate(), order.account
    order.status, order.paid_at = Order.Status.PAID, timezone.now()
    if payment is not None:
        order.payment_method = payment.provider
    order.save(update_fields=["status", "paid_at", "payment_method"])

    last_end = None
    for line in order.lines.select_related("plan__product", "subscription"):
        subscription, plan = line.subscription, line.plan
        if subscription is None:
            continue
        running = subscription.status in Subscription.LIVE and subscription.period_end and subscription.period_end >= today
        start = subscription.period_end if order.kind == Order.Kind.RENEWAL and running else today
        line.period_from, line.period_to = start, start + TERM
        line.save(update_fields=["period_from", "period_to"])
        # A newly bought plan replaces whatever the account had of the same product.
        account.subscriptions.exclude(pk=subscription.pk).filter(status__in=Subscription.LIVE, plan__product=plan.product).update(
            status=Subscription.Status.ENDED
        )
        subscription.status = Subscription.Status.ACTIVE
        subscription.period_end = line.period_to
        if payment is not None:
            subscription.provider, subscription.provider_ref = payment.provider, payment.provider_id[:120]
        subscription.save()
        licensing.grant(account, plan, source="subscription", valid_until=line.period_to)
        last_end = line.period_to
    audit("billing.paid", account=account, obj=order.ref, data={"plan": order.plan.code if order.plan else "", "until": str(last_end)})

    invoice = invoicing.invoice_for(order)
    if invoice is None:
        try:
            with transaction.atomic():
                invoice = invoicing.issue_invoice(order)
        except Exception as exc:  # noqa: BLE001 - a missing PDF library must not undo a payment
            log.exception("invoice for %s could not be created", order.ref)
            flag(ReconciliationIssue.Kind.INVOICE_MISSING, "", order.ref, f"{order.ref}: {exc}", order)
    mails.order_confirmation(order, last_end, invoice)
    return order


def payment_failed(order: Order) -> None:
    """A payment attempt failed. A new order is given up; a renewal stays due."""
    if order.status != Order.Status.PENDING or order.kind == Order.Kind.RENEWAL:
        return
    if order.payments.filter(status__in=OPEN).exists():
        return
    order.status = Order.Status.FAILED
    order.save(update_fields=["status"])
    for line in order.lines.select_related("subscription"):
        if line.subscription and line.subscription.status == Subscription.Status.INCOMPLETE:
            line.subscription.status = Subscription.Status.ENDED
            line.subscription.save(update_fields=["status", "updated_at"])
    audit("billing.payment_failed", account=order.account, obj=order.ref)


@transaction.atomic
def cancel_order(order: Order, *, by=None, kind: str = "cancelled", source: str = PaymentEvent.Source.STAFF) -> Order:
    """Give up an unpaid order: open payments are closed, an invoice already issued is voided."""
    from apps.invoicing import services as invoicing

    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.Status.PENDING:
        return order
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status"])
    for payment in order.payments.filter(status__in=OPEN):
        events.set_status(payment, PaymentStatus.CANCELLED, kind=kind, source=source, actor=by)
    for line in order.lines.select_related("subscription"):
        if line.subscription and line.subscription.status == Subscription.Status.INCOMPLETE:
            line.subscription.status = Subscription.Status.ENDED
            line.subscription.save(update_fields=["status", "updated_at"])
    invoice = invoicing.invoice_for(order)
    if invoice is not None and not invoicing.is_voided(invoice):
        invoicing.void_invoice(invoice, reason="Order cancelled before payment")
    audit("billing.order_cancelled", actor=by, account=order.account, obj=order.ref, data={"why": kind})
    return order


# --------------------------------------------------------------------------- refunds


@transaction.atomic
def refund_order(order: Order, *, amount: Decimal | None = None, reason: str = "", by=None) -> Refund:
    """Refund against the original payment and issue a credit note. A full refund ends the plan."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status not in (Order.Status.PAID, Order.Status.PARTIALLY_REFUNDED):
        raise RefundError("The order is not paid.")
    payment = order.payments.filter(status__in=[PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED]).order_by("id").first()
    if payment is None:
        raise RefundError("There is no successful payment to refund.")
    refundable = payment.amount - order.refunded_total
    amount = Decimal(amount) if amount is not None else refundable
    if amount <= 0 or amount > refundable:
        raise RefundError(f"The amount exceeds what is left to refund ({refundable}).")
    try:
        result = get_provider(payment.provider).refund(payment, amount)
    except ProviderError as exc:
        raise RefundError(str(exc)) from exc
    history = {"kind": "refund", "source": PaymentEvent.Source.STAFF if by else PaymentEvent.Source.SYSTEM, "actor": by}
    return _book_refund(order, payment, amount, provider_id=result.provider_id, reason=reason, by=by, full=amount == refundable, history=history)


def record_external_refund(payment, amount, *, provider_id: str, reason: str, details: dict):
    """Money went back at the provider without us (dashboard, provider decision): book it like
    our own refund, credit note included."""
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    refundable = payment.amount - order.refunded_total
    amount = min(Decimal(amount), refundable)
    if amount <= 0:
        events.record(payment, kind="refund_external", outcome="nothing left to refund", **details)
        return None
    history = {**details, "kind": "refund_external"}
    history.pop("amount", None)
    return _book_refund(order, payment, amount, provider_id=provider_id[:100], reason=reason[:300], by=None, full=amount == refundable, history=history)


def _book_refund(order, payment, amount, *, provider_id, reason, by, full, history) -> Refund:
    from apps.invoicing import services as invoicing

    refund = Refund.objects.create(order=order, payment=payment, amount=amount, reason=reason[:300], provider_id=provider_id, created_by=by)
    events.set_status(
        payment,
        PaymentStatus.REFUNDED if full else PaymentStatus.PARTIALLY_REFUNDED,
        amount=amount,
        note=reason[:300],
        outcome=provider_id[:120],
        **history,
    )
    order.status = Order.Status.REFUNDED if full else Order.Status.PARTIALLY_REFUNDED
    order.save(update_fields=["status"])
    credit_note = None
    invoice = invoicing.invoice_for(order)
    if invoice is not None:
        try:
            with transaction.atomic():
                credit_note = invoicing.issue_credit_note(invoice, amount, reason=reason, full=full and not order.refunds.exclude(pk=refund.pk).exists())
        except Exception as exc:  # noqa: BLE001
            log.exception("credit note for %s could not be created", order.ref)
            flag(ReconciliationIssue.Kind.INVOICE_MISSING, "", f"{order.ref}-refund-{refund.pk}", f"credit note: {exc}", order)
    if full:
        # Everything went back (withdrawal, goodwill): the plans of this order end today.
        for line in order.lines.select_related("plan__product", "subscription"):
            subscription = line.subscription
            if subscription and subscription.status in Subscription.LIVE:
                subscription.status = Subscription.Status.ENDED
                subscription.save(update_fields=["status", "updated_at"])
                licensing.grant_free(order.account, line.plan.product)
    audit("billing.refunded", actor=by, account=order.account, obj=order.ref, data={"amount": str(amount), "full": full})
    mails.refund_notice(order, refund, credit_note, manual=payment.provider == "bank_transfer")
    return refund


# --------------------------------------------------------------------------- bank statements


@dataclass
class BankRow:
    date: str
    amount: Decimal
    purpose: str
    name: str = ""


@dataclass
class ImportReport:
    matched: list[tuple[BankRow, Order]] = field(default_factory=list)
    unmatched: list[BankRow] = field(default_factory=list)
    problems: list[tuple[BankRow, str]] = field(default_factory=list)


class StatementError(ValueError):
    """The uploaded file is neither a readable CAMT.053 nor a CSV statement."""


def _amount(text: str) -> Decimal | None:
    text = text.strip().replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_statement(content: bytes) -> list[BankRow]:
    """Parse a bank statement: CAMT.053 XML or a CSV with date/amount/purpose columns."""
    try:
        return _parse_statement(content)
    except (ParseError, DefusedXmlException, ValueError, ArithmeticError, csv.Error) as exc:
        raise StatementError(str(exc)) from exc


def _parse_statement(content: bytes) -> list[BankRow]:
    text = content.decode("utf-8-sig", errors="replace")
    if text.lstrip().startswith("<"):
        return _parse_camt(text)
    sample = text[:2000]
    dialect = csv.Sniffer().sniff(sample, delimiters=";,\t") if sample else csv.excel
    rows: list[BankRow] = []
    for raw in csv.DictReader(io.StringIO(text), dialect=dialect):
        row = {k.strip().lower(): (v or "") for k, v in raw.items() if k}

        def pick(*keys, row=row):
            return next((row[k] for k in keys if k in row), "")

        amount = _amount(pick("betrag", "amount", "umsatz"))
        if amount is None:
            continue
        rows.append(
            BankRow(
                date=pick("buchungstag", "date", "datum", "buchungsdatum"),
                amount=amount,
                purpose=pick("verwendungszweck", "purpose", "reference", "buchungstext"),
                name=pick("beguenstigter/zahlungspflichtiger", "name", "auftraggeber"),
            )
        )
    return rows


def _parse_camt(text: str) -> list[BankRow]:
    text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)
    root = safe_xml(text)  # no entities, no external references (defusedxml)
    rows = []
    for entry in root.iter("Ntry"):
        if (entry.findtext("CdtDbtInd") or "") != "CRDT":
            continue
        amount = Decimal(entry.findtext("Amt") or "0")
        purpose = " ".join(t.text or "" for t in entry.iter("Ustrd"))
        name = entry.findtext(".//RltdPties/Dbtr/Nm") or ""
        rows.append(BankRow(entry.findtext("BookgDt/Dt") or "", amount, purpose, name))
    return rows


_ORDER_REF = re.compile(r"(?<![A-Z0-9])CO-[A-HJ-NP-Z2-9]{7}(?![A-Z0-9])")
_ORDER_REF_GLUED = re.compile(r"(?=(CO-[A-HJ-NP-Z2-9]{7}))")


def find_order(purpose: str) -> Order | None:
    """Find the order a transfer refers to. Banks wrap and re-space the reference, so try the
    text as written first, then with all whitespace removed (where the number may be glued to
    its neighbours). Only a number that exists counts, and the amount has to match as well."""
    text = purpose.upper()
    compact = re.sub(r"[\s.]+", "", text)
    candidates = [m.group(0) for m in _ORDER_REF.finditer(text)] + [m.group(1) for m in _ORDER_REF_GLUED.finditer(compact)]
    for ref in candidates:
        order = Order.objects.filter(ref=ref).first()
        if order is not None:
            return order
    return None


def import_statement(content: bytes, by=None) -> ImportReport:
    """Match incoming transfers to open orders by order number and amount."""
    report = ImportReport()
    for row in parse_statement(content):
        if row.amount <= 0:
            continue
        order = find_order(row.purpose)
        if order is None:
            report.unmatched.append(row)
        elif order.status in Order.SETTLED:
            report.problems.append((row, f"{order.ref} is already paid"))
        elif row.amount != order.total:
            report.problems.append((row, f"{order.ref}: expected {order.total}, got {row.amount}"))
        else:
            confirm_bank_payment(order, row, by=by)
            report.matched.append((row, order))
    return report


@transaction.atomic
def confirm_bank_payment(order: Order, row: BankRow | None = None, by=None) -> Order:
    """The money is on the bank account: found in a statement, or confirmed by a person."""
    payment = order.payments.filter(provider="bank_transfer").exclude(status__in=COLLECTED).last()
    if payment is None:
        payment = Payment.objects.create(order=order, provider="bank_transfer", amount=order.total)
    payment.provider_id = payment.provider_id or f"bank-{order.ref}-{payment.pk}"
    statement = {**vars(row), "amount": str(row.amount)} if row else {"manual": True}
    payment.raw = {"statement": statement, "by": getattr(by, "pk", None)}
    payment.save()
    events.set_status(
        payment,
        PaymentStatus.SUCCEEDED,
        kind="bank_statement" if row else "marked_paid",
        source=PaymentEvent.Source.IMPORT if row else PaymentEvent.Source.STAFF,
        actor=by,
        amount=row.amount if row else payment.amount,
        note=(row.purpose[:300] if row else ""),
    )
    return mark_paid(order, payment)


# --------------------------------------------------------------------------- reconciliation


def reconcile(days: int = 3) -> int:
    """Compare our payments with what Stripe and PayPal list themselves; record every difference."""
    cfg, since, issues = config(), timezone.now() - timedelta(days=days), 0
    for code in ("stripe", "paypal"):
        if not is_configured(code, cfg):
            continue
        try:
            rows = get_provider(code).ledger(cfg, since)
        except ProviderError as exc:
            issues += flag(ReconciliationIssue.Kind.UNKNOWN, code, "ledger", f"ledger not readable: {exc}")
            continue
        if rows is None:
            continue
        theirs = {provider_id: (amount, ref) for provider_id, amount, ref in rows}
        ours = {p.provider_id: p for p in Payment.objects.filter(provider=code, status__in=COLLECTED, created_at__gte=since).select_related("order")}
        for provider_id, payment in ours.items():
            remote = theirs.get(provider_id)
            if remote is None:
                issues += flag(ReconciliationIssue.Kind.MISSING_REMOTE, code, provider_id, f"{payment.amount} for {payment.order.ref}", payment.order)
            elif remote[0] != payment.amount:
                issues += flag(ReconciliationIssue.Kind.AMOUNT, code, provider_id, f"ours {payment.amount}, provider {remote[0]}", payment.order)
        for provider_id, (amount, ref) in theirs.items():
            # Only payments that carry one of our order numbers: the account may be used for other things too.
            if provider_id not in ours and ref and Order.objects.filter(ref=ref).exists():
                issues += flag(ReconciliationIssue.Kind.MISSING_LOCAL, code, provider_id, f"{amount} for {ref}")
    if issues:
        alerts.alert("reconciliation", detail=f"{issues} new difference(s)")
    return issues
