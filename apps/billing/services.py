"""Products, plans, checkout and the life of a paid plan.

A paid plan runs for twelve months and is paid in advance, term by term: every term is an
order with one payment and one invoice. Nothing is charged automatically. Before a term ends
the customer gets the invoice for the next one with a link to pay it.

The browser never tells us that a payment succeeded; see :mod:`apps.billing.payments`.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.log import audit
from apps.licensing import services as licensing

from . import alerts, mails, tax
from .models import Order, OrderLine, Plan, Product, ReconciliationIssue, Subscription
from .payments import cancel_order, flag, process_webhook
from .providers import config, get_provider, usable_methods

handle_webhook = process_webhook  # the name the first version used


class CheckoutError(Exception):
    pass


def public_plans(product=None):
    """The plans shown on the site, of one product (default: the main one)."""
    product = product or Product.default()
    return Plan.objects.filter(is_public=True, product=product).select_related("product")


def public_products():
    return Product.objects.filter(is_public=True)


def active_subscription(account, product=None) -> Subscription | None:
    product = product or Product.default()
    return account.subscriptions.filter(status__in=Subscription.LIVE, plan__product=product).select_related("plan__product").first()


def subscriptions(account):
    return account.subscriptions.filter(status__in=Subscription.LIVE).select_related("plan__product")


def line_text(plan: Plan, language: str) -> str:
    if (language or "").startswith("de"):
        return f"{plan.product.name} {plan.name}, Support-Tarif für zwölf Monate"
    return f"{plan.product.name} {plan.name}, support plan for twelve months"


def _new_order(account, user, plan, subscription, *, kind, language, **fields) -> Order:
    from apps.invoicing.services import buyer_of

    price = Decimal(plan.price_eur_year)
    order = Order.objects.create(
        account=account,
        placed_by=user,
        kind=kind,
        total=price,
        language=language,
        billing=buyer_of(account, user),
        terms_version=settings.COMPANY["LEGAL_AS_OF"],
        **fields,
    )
    OrderLine.objects.create(
        order=order,
        plan=plan,
        subscription=subscription,
        description=line_text(plan, language),
        unit_price=price,
        tax_rate=tax.rate_for(account),
        total=price,
    )
    return order


@transaction.atomic
def start_checkout(account, user, plan: Plan, request=None, *, early_start: bool = False, method: str | None = None) -> tuple[Order, str]:
    """Create a pending order and return it with the address where the customer pays.

    early_start: a consumer expressly asked for support to begin within the withdrawal period.
    """
    if plan.price_eur_year <= 0 or not plan.self_service:
        raise CheckoutError(f"{plan.name} cannot be bought in the shop.")
    codes = [m.code for m in usable_methods()]
    if not codes:
        raise CheckoutError("No payment method is available at the moment. Please write to us.")
    method = method or codes[0]
    if method not in codes:
        raise CheckoutError("This payment method is not available.")
    language = user.locale if user and user.locale in ("de", "en") else "en"
    subscription = Subscription.objects.create(account=account, plan=plan, provider=method)
    due = timezone.localdate() + timedelta(days=settings.CASSILION["BANK_TRANSFER_DAYS"]) if method == "bank_transfer" else None
    order = _new_order(
        account, user, plan, subscription, kind=Order.Kind.NEW, language=language, payment_method=method, due_date=due,
        early_start_requested_at=timezone.now() if early_start else None,
    )
    audit("billing.checkout_started", actor=user, account=account, obj=order.ref, data={"plan": plan.code, "method": method})
    return order, pay(order, method, request)


def pay(order: Order, method: str, request=None) -> str:
    """Start one payment attempt for a pending order; returns where the customer goes next."""
    if order.status != Order.Status.PENDING:
        raise CheckoutError("This order is not open any more.")
    if method not in [m.code for m in usable_methods()]:
        raise CheckoutError("This payment method is not available.")
    from .providers import ProviderError

    if order.payment_method != method:
        order.payment_method = method
        order.save(update_fields=["payment_method"])
    try:
        result = get_provider(method).start(order, request)
    except ProviderError as exc:
        raise CheckoutError(f"The payment could not be started: {exc}") from exc
    return result.redirect_url


def take_free_plan(account, user, plan: Plan):
    """A free license of a product the account does not have yet: no order, no payment."""
    if not plan.is_free or not plan.is_public:
        raise CheckoutError(f"{plan.name} is not a free plan.")
    current = licensing.current_entitlement(account, plan.product)
    if current and current.plan.rank >= plan.rank:
        return current
    audit("billing.free_plan_taken", actor=user, account=account, data={"plan": plan.code})
    return licensing.grant(account, plan, source="free")


@transaction.atomic
def cancel(subscription: Subscription, user) -> None:
    """The customer cancels. The plan runs to the end of the paid period, then falls back to the free plan.

    An invoice already sent for the next term is voided with it.
    """
    if subscription.status not in {Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE}:
        return
    subscription.status = Subscription.Status.CANCELLED
    subscription.save(update_fields=["status", "updated_at"])
    for order in Order.objects.filter(kind=Order.Kind.RENEWAL, status=Order.Status.PENDING, lines__subscription=subscription).distinct():
        cancel_order(order, by=user, kind="cancelled", source="customer")
    audit("billing.cancel_requested", actor=user, account=subscription.account, obj=str(subscription.pk))


# --------------------------------------------------------------------------- the daily job


def _open_renewal(subscription) -> Order | None:
    return Order.objects.filter(kind=Order.Kind.RENEWAL, status=Order.Status.PENDING, lines__subscription=subscription).first()


@transaction.atomic
def create_renewal(subscription: Subscription) -> Order:
    """The order and invoice for the next twelve months of a running plan, due when the paid term ends."""
    from apps.invoicing import services as invoicing

    plan, account = subscription.plan, subscription.account
    previous = Order.objects.filter(lines__subscription=subscription).order_by("-created_at").first()
    user = previous.placed_by if previous else None
    language = previous.language if previous else "en"
    order = _new_order(account, user, plan, subscription, kind=Order.Kind.RENEWAL, language=language, due_date=subscription.period_end)
    line = order.lines.get()
    line.period_from, line.period_to = subscription.period_end, subscription.period_end + timedelta(days=365)
    line.save(update_fields=["period_from", "period_to"])
    invoice = None
    try:
        with transaction.atomic():
            invoice = invoicing.issue_invoice(order, due_date=order.due_date)
    except Exception as exc:  # noqa: BLE001 - the notice still goes out; a person adds the invoice
        flag(ReconciliationIssue.Kind.INVOICE_MISSING, "", order.ref, f"{order.ref}: {exc}", order)
    audit("billing.renewal_created", account=account, obj=order.ref, data={"plan": plan.code, "due": str(order.due_date)})
    mails.renewal_notice(order, invoice, config(), subscription.period_end)
    return order


def renew_due(today=None) -> int:
    """Send the invoice for the next term to every running paid plan that ends soon."""
    today = today or timezone.localdate()
    horizon = today + timedelta(days=settings.CASSILION["RENEWAL_NOTICE_DAYS"])
    created = 0
    for subscription in Subscription.objects.select_related("plan__product", "account").filter(
        status=Subscription.Status.ACTIVE, period_end__isnull=False, period_end__lte=horizon, period_end__gte=today
    ):
        plan = subscription.plan
        if plan.price_eur_year <= 0 or not plan.self_service or _open_renewal(subscription):
            continue  # plans by quote are renewed by a person
        create_renewal(subscription)
        created += 1
    return created


@transaction.atomic
def expire_due(today=None) -> int:
    """Subscriptions past their period return to the free plan: cancelled ones at once, unpaid
    renewals after a reminder and the grace period. Open bank-transfer orders that were never
    paid are given up."""
    from apps.invoicing.services import invoice_for

    today = today or timezone.localdate()
    grace = timedelta(days=settings.CASSILION["GRACE_DAYS"])
    ended = 0
    for subscription in Subscription.objects.select_related("account", "plan__product").filter(status__in=Subscription.LIVE, period_end__lt=today):
        renewal = _open_renewal(subscription)
        if subscription.status == Subscription.Status.ACTIVE and renewal is not None:
            # The next term is invoiced but not paid: remind once, keep the plan through the grace period.
            subscription.status = Subscription.Status.PAST_DUE
            subscription.save(update_fields=["status", "updated_at"])
            audit("billing.past_due", account=subscription.account, obj=renewal.ref)
            mails.renewal_notice(renewal, invoice_for(renewal), config(), subscription.period_end, reminder=True)
            continue
        if subscription.status == Subscription.Status.PAST_DUE and subscription.period_end + grace >= today:
            continue
        subscription.status = Subscription.Status.ENDED
        subscription.save(update_fields=["status", "updated_at"])
        licensing.grant_free(subscription.account, subscription.plan.product)
        audit("billing.ended", account=subscription.account, obj=str(subscription.pk))
        if renewal is not None:
            # The invoice stays as it is: whether to collect it or void it is a person's decision.
            if flag(ReconciliationIssue.Kind.UNPAID_INVOICE, "", renewal.ref, f"{renewal.ref}: {renewal.total} not paid, plan ended", renewal):
                alerts.alert("unpaid_invoice", order=renewal, detail=str(renewal.total))
        ended += 1
    for order in Order.objects.filter(kind=Order.Kind.NEW, status=Order.Status.PENDING, due_date__lt=today):
        cancel_order(order, kind="expired", source="job")
    return ended
