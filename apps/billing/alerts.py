"""Tell the people who handle money at once when a payment needs a person: a chargeback opened
or lost, an amount that does not match, or webhooks that keep failing."""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import PaymentEvent

FAILURES_BEFORE_ALERT = 3  # rejected webhooks of one provider ...
FAILURE_WINDOW = 3600  # ... within this many seconds
QUIET = 3600  # at most one failure alert per provider and hour

SUBJECTS = {
    "amount_mismatch": "Payment with a different amount",
    "dispute_opened": "Chargeback or dispute opened",
    "dispute_lost": "Dispute lost, money charged back",
    "webhook_failures": "Payment webhooks are being rejected",
    "reconciliation": "Payments differ from the provider's records",
    "late_payment": "Money arrived for a cancelled order",
    "unpaid_invoice": "Renewal invoice not paid",
}


def finance_addresses() -> list[str]:
    from apps.billing.providers import config

    address = config().finance_email
    return [address] if address else list(settings.CASSILION["OPERATOR_EMAILS"])


def alert(kind: str, *, order=None, provider: str = "", detail: str = "") -> None:
    """One mail without customer data beyond the order number; the details are in the backoffice."""
    to = finance_addresses()
    if not to:
        return
    path = reverse("backoffice:order", args=[order.ref]) if order else reverse("backoffice:payment_issues")
    lines = [SUBJECTS.get(kind, kind) + ".", ""]
    if order:
        lines.append(f"Order: {order.ref}")
    if provider:
        lines.append(f"Provider: {provider}")
    if detail:
        lines.append(f"Detail: {detail[:300]}")
    lines += ["", settings.CASSILION["SITE_URL"].rstrip("/") + path]
    send_mail(f"[Cassilion] {SUBJECTS.get(kind, kind)}", "\n".join(lines), settings.DEFAULT_FROM_EMAIL, to, fail_silently=True)


def webhook_rejected(provider: str, reason: str) -> None:
    """Count rejected webhooks; alert when they pile up (wrong secret, changed endpoint ...)."""
    since = timezone.now() - timedelta(seconds=FAILURE_WINDOW)
    recent = PaymentEvent.objects.filter(provider=provider, kind="rejected", created_at__gte=since).count()
    if recent < FAILURES_BEFORE_ALERT:
        return
    if not cache.add(f"webhook-alert:{provider}", 1, QUIET):
        return
    alert("webhook_failures", provider=provider, detail=f"{recent} × {reason}")
