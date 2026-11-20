"""Plans, checkout and the subscription lifecycle.

The browser never tells us that a payment succeeded. Only a provider event,
verified and applied exactly once, changes a subscription or an entitlement.
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.log import audit
from apps.licensing import services as licensing

from .models import Order, PaymentEvent, Plan, Subscription
from .providers import ProviderEvent, WebhookError, get_provider
from .providers.base import EVENT_CANCELLED, EVENT_FAILED, EVENT_PAID, EVENT_PAST_DUE


class CheckoutError(Exception):
    pass


def public_plans():
    return Plan.objects.filter(is_public=True)


def active_subscription(account) -> Subscription | None:
    return account.subscriptions.filter(
        status__in=[Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE, Subscription.Status.CANCELLED]
    ).first()


@transaction.atomic
def start_checkout(account, user, plan: Plan, request=None, *, early_start: bool = False) -> tuple[Order, str]:
    """Create a pending order and return it with the provider's checkout URL.

    early_start: a consumer expressly asked for support to begin within the withdrawal period.
    """
    if plan.price_eur_year <= 0 or not plan.self_service:
        raise CheckoutError(f"{plan.name} cannot be bought in the shop.")
    provider = get_provider()
    subscription = Subscription.objects.create(account=account, plan=plan, provider=provider.name)
    order = Order.objects.create(
        account=account,
        subscription=subscription,
        plan=plan,
        placed_by=user,
        amount_eur=plan.price_eur_year,
        early_start_requested_at=timezone.now() if early_start else None,
        terms_version=settings.COMPANY["LEGAL_AS_OF"],
    )
    audit("billing.checkout_started", actor=user, account=account, obj=order.ref, data={"plan": plan.code})
    return order, provider.create_checkout(order, request)


def handle_webhook(provider_name: str, body: bytes, headers) -> PaymentEvent:
    """Verify, store and apply one provider event. Safe to call twice with the same event."""
    event = get_provider(provider_name).parse_webhook(body, headers)
    try:
        with transaction.atomic():
            record = PaymentEvent.objects.create(
                provider=provider_name, event_id=event.event_id, type=event.type, payload=event.raw
            )
    except IntegrityError:
        return PaymentEvent.objects.get(provider=provider_name, event_id=event.event_id)

    try:
        with transaction.atomic():
            _apply(event)
            record.processed_at = timezone.now()
            record.save(update_fields=["processed_at"])
    except Order.DoesNotExist:
        record.error = "unknown order"
        record.save(update_fields=["error"])
        raise WebhookError("Unknown order.") from None
    return record


def _apply(event: ProviderEvent) -> None:
    order = Order.objects.select_for_update().select_related("subscription", "plan", "account").get(ref=event.order_ref)
    subscription, account, today = order.subscription, order.account, timezone.localdate()

    if event.type == EVENT_PAID:
        period_end = event.period_end or today + timedelta(days=365)
        order.status, order.paid_at = Order.Status.PAID, timezone.now()
        order.save(update_fields=["status", "paid_at"])
        # A new paid plan replaces whatever the account had before.
        account.subscriptions.exclude(pk=subscription.pk).filter(
            status__in=[Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE, Subscription.Status.CANCELLED]
        ).update(status=Subscription.Status.ENDED)
        subscription.status = Subscription.Status.ACTIVE
        subscription.provider_ref = event.subscription_ref
        subscription.period_end = period_end
        subscription.save()
        licensing.grant(account, order.plan, source="subscription", valid_until=period_end)
        audit("billing.paid", account=account, obj=order.ref, data={"plan": order.plan.code, "until": str(period_end)})
        _confirm_order(order, period_end)

    elif event.type == EVENT_FAILED:
        order.status = Order.Status.FAILED
        order.save(update_fields=["status"])
        if subscription.status == Subscription.Status.INCOMPLETE:
            subscription.status = Subscription.Status.ENDED
            subscription.save(update_fields=["status", "updated_at"])
        audit("billing.payment_failed", account=account, obj=order.ref)

    elif event.type == EVENT_PAST_DUE:
        subscription.status = Subscription.Status.PAST_DUE
        subscription.save(update_fields=["status", "updated_at"])
        audit("billing.past_due", account=account, obj=order.ref)

    elif event.type == EVENT_CANCELLED:
        subscription.status = Subscription.Status.CANCELLED
        subscription.save(update_fields=["status", "updated_at"])
        audit("billing.cancelled", account=account, obj=order.ref)


def cancel(subscription: Subscription, user) -> None:
    """The customer cancels. The plan runs to the end of the paid period, then falls back to Community."""
    if subscription.status not in {Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE}:
        return
    subscription.status = Subscription.Status.CANCELLED
    subscription.save(update_fields=["status", "updated_at"])
    audit("billing.cancel_requested", actor=user, account=subscription.account, obj=str(subscription.pk))


@transaction.atomic
def expire_due(today=None) -> int:
    """Daily job: subscriptions past their period (plus grace for overdue ones) return to Community."""
    today = today or timezone.localdate()
    grace = timedelta(days=settings.CASSILION["GRACE_DAYS"])
    ended = 0
    for subscription in Subscription.objects.select_related("account").filter(
        status__in=[Subscription.Status.CANCELLED, Subscription.Status.PAST_DUE], period_end__isnull=False
    ):
        limit = subscription.period_end + (grace if subscription.status == Subscription.Status.PAST_DUE else timedelta(0))
        if limit < today:
            subscription.status = Subscription.Status.ENDED
            subscription.save(update_fields=["status", "updated_at"])
            licensing.grant_community(subscription.account)
            audit("billing.ended", account=subscription.account, obj=str(subscription.pk))
            ended += 1
    return ended


def _confirm_order(order: Order, period_end) -> None:
    """The order confirmation. It carries the terms and the withdrawal policy as files.

    German consumer law wants the confirmation of the contract, with its terms and the
    instructions on withdrawal, on a durable medium. A link to a web page is not one.
    """
    from apps.core import legal  # imported here: core imports billing for the home page

    user = order.placed_by
    if not user:
        return
    lang = user.locale if user.locale in legal.LANGUAGES else "en"
    company, site = settings.COMPANY, settings.CASSILION["SITE_URL"]
    if lang == "de":
        lines = [
            f"Guten Tag {user.get_short_name()},",
            "",
            "vielen Dank für Ihre Bestellung. Wir bestätigen den Vertrag:",
            "",
            f"Bestellnummer: {order.ref}",
            f"Tarif: {order.plan.name}, Laufzeit zwölf Monate bis {period_end:%d.%m.%Y}",
            f"Preis: {order.amount_eur} EUR pro Jahr, einschließlich Umsatzsteuer",
            f"Kundenkonto: {order.account.name}",
            "",
            "Der Tarif ist freigeschaltet. Eine neue Lizenzdatei liegt in Ihrem Kundenkonto bereit.",
        ]
        if order.early_start_requested_at:
            lines += [
                "",
                "Sie haben bei der Bestellung ausdrücklich verlangt, dass wir mit dem Support vor Ablauf der "
                "Widerrufsfrist beginnen. Widerrufen Sie den Vertrag, zahlen Sie für die bis dahin erbrachte Leistung "
                "einen anteiligen Betrag.",
            ]
        lines += [
            "",
            "Im Anhang finden Sie unsere Allgemeinen Geschäftsbedingungen und die Widerrufsbelehrung mit dem Muster-Widerrufsformular.",
            f"Vertrag widerrufen: {site}/legal/withdraw/?lang=de",
            f"Verträge hier kündigen: {site}/legal/cancel/?lang=de",
            "",
            "Mit freundlichen Grüßen",
        ]
        subject = f"[Cassilion] Bestellbestätigung {order.ref}"
    else:
        lines = [
            f"Hello {user.get_short_name()},",
            "",
            "Thank you for your order. We confirm the contract:",
            "",
            f"Order number: {order.ref}",
            f"Plan: {order.plan.name}, term twelve months until {period_end:%d %B %Y}",
            f"Price: EUR {order.amount_eur} per year, including VAT",
            f"Customer account: {order.account.name}",
            "",
            "The plan is active. A new license file is ready in your account.",
        ]
        if order.early_start_requested_at:
            lines += [
                "",
                "When ordering you expressly asked us to start support before the withdrawal period ends. If you "
                "withdraw from the contract, you pay a proportionate amount for what has been provided until then.",
            ]
        lines += [
            "",
            "Attached are our terms and conditions and the withdrawal policy with the model withdrawal form.",
            f"Withdraw from contract: {site}/legal/withdraw/?lang=en",
            f"Cancel contracts here: {site}/legal/cancel/?lang=en",
            "",
            "Kind regards",
        ]
        subject = f"[Cassilion] Order confirmation {order.ref}"
    lines += [company["NAME"], f"{company['STREET']}, {company['POSTAL_CODE']} {company['CITY']}", company["EMAIL"]]
    message = EmailMessage(subject, "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [user.email], reply_to=[company["EMAIL"]])
    for name, content, mimetype in legal.documents(lang):
        message.attach(name, content, mimetype)
    message.send(fail_silently=True)
