"""Plans, checkout and the subscription lifecycle.

The browser never tells us that a payment succeeded. Only a provider event,
verified and applied exactly once, changes a subscription or an entitlement.
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
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
def start_checkout(account, user, plan: Plan, request=None) -> tuple[Order, str]:
    """Create a pending order and return it with the provider's checkout URL."""
    if plan.price_eur_year <= 0 or not plan.self_service:
        raise CheckoutError(f"{plan.name} cannot be bought in the shop.")
    provider = get_provider()
    subscription = Subscription.objects.create(account=account, plan=plan, provider=provider.name)
    order = Order.objects.create(
        account=account, subscription=subscription, plan=plan, placed_by=user, amount_eur=plan.price_eur_year
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
        _notify(order, f"Your {order.plan.name} plan is active until {period_end:%d %B %Y}. A new license file is ready in your account.")

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


def _notify(order: Order, text: str) -> None:
    recipient = order.placed_by.email if order.placed_by else None
    if recipient:
        send_mail(f"[Cassilion] {order.plan.name} plan", text, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=True)
