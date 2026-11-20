import logging

from django import forms
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.decorators import account_required
from apps.accounts.models import Account, Address
from apps.audit.log import audit
from apps.core.pdf import pdf_view
from apps.invoicing.models import Invoice
from apps.licensing import services as licensing

from . import payments, services
from .models import OPEN, Order, Payment, PaymentEvent, Plan, Product, Subscription
from .providers import ProviderError, WebhookRejected, config, get_provider, usable_methods
from .providers.bank import instructions
from .providers.base import EVENT_FAILED, EVENT_PAID

log = logging.getLogger(__name__)


class BillingAddressForm(forms.ModelForm):
    """What an invoice needs. Asked on the order page, kept on the account."""

    class Meta:
        model = Address
        fields = ["line1", "line2", "postal_code", "city"]
        labels = {"line1": "Street and number", "line2": "Address line 2 (optional)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("line1", "postal_code", "city"):
            self.fields[name].required = True


def pricing(request):
    account = request.account if request.user.is_authenticated else None
    products = []
    for product in services.public_products():
        entitlement = licensing.current_entitlement(account, product) if account else None
        products.append({"product": product, "plans": services.public_plans(product), "current_plan": entitlement.plan if entitlement else None})
    return render(request, "billing/pricing.html", {"products": products, "default_product": Product.default()})


@account_required
def overview(request):
    account = request.account
    by_product = {s.plan.product_id: s for s in services.subscriptions(account)}
    holdings = [{"entitlement": e, "subscription": by_product.get(e.product_id)} for e in licensing.entitlements(account)]
    return render(
        request,
        "billing/overview.html",
        {
            "holdings": holdings,
            "orders": account.orders.prefetch_related("lines__plan__product")[:20],
            "invoices": account.invoices.all()[:20],
        },
    )


@account_required
def checkout_start(request, code):
    """The order page: what is ordered, what it costs, how it ends; then the binding order button."""
    plan = get_object_or_404(Plan.objects.select_related("product"), code=code, is_public=True, product__is_public=True)
    account = request.account
    if plan.price_eur_year <= 0 or not plan.self_service:
        messages.error(request, f"{plan.name} cannot be bought in the shop.")
        return redirect("billing:pricing")
    methods = usable_methods()
    # Only consumers have a right of withdrawal, so only they are asked about an early start.
    is_consumer = account.kind == Account.Kind.INDIVIDUAL
    address, _ = Address.objects.get_or_create(account=account)
    form = BillingAddressForm(request.POST or None, instance=address)
    error = ""
    if request.method == "POST":
        early_start = request.POST.get("early_start") == "on"
        method = request.POST.get("method") or (methods[0].code if methods else "")
        if is_consumer and not early_start:
            error = "Please confirm that support should start right away. Without this we cannot activate the plan before the withdrawal period has ended."
        elif form.is_valid():
            form.save()
            try:
                _order, url = services.start_checkout(account, request.user, plan, request, early_start=is_consumer and early_start, method=method)
            except services.CheckoutError as exc:
                messages.error(request, str(exc))
                return redirect("billing:pricing")
            return redirect(url)
    return render(request, "billing/checkout.html", {"plan": plan, "is_consumer": is_consumer, "error": error, "methods": methods, "form": form})


@account_required
@require_POST
def free_plan(request, code):
    plan = get_object_or_404(Plan.objects.select_related("product"), code=code, is_public=True, product__is_public=True)
    try:
        services.take_free_plan(request.account, request.user, plan)
    except services.CheckoutError as exc:
        messages.error(request, str(exc))
        return redirect("billing:pricing")
    messages.success(request, f"Your {plan.product.name} license is ready.")
    return redirect("licensing:licenses")


@account_required
def order_detail(request, ref):
    """One order: what was ordered, where the payment stands, how to pay, the documents."""
    order = get_object_or_404(Order.objects.prefetch_related("lines__plan__product", "payments"), ref=ref, account=request.account)
    pending = order.status == Order.Status.PENDING
    transfer = pending and order.payments.filter(provider="bank_transfer", status__in=OPEN).exists()
    return render(
        request,
        "billing/order.html",
        {
            "order": order,
            "pending": pending,
            "methods": usable_methods() if pending else [],
            "bank": instructions(order, config()) if transfer or (pending and order.kind == Order.Kind.RENEWAL and config().iban) else None,
            "processing": order.payments.filter(status="processing").exists(),
            "invoices": order.invoices.all(),
        },
    )


@account_required
@require_POST
def order_pay(request, ref):
    order = get_object_or_404(Order, ref=ref, account=request.account)
    try:
        return redirect(services.pay(order, request.POST.get("method", ""), request))
    except services.CheckoutError as exc:
        messages.error(request, str(exc))
        return redirect("billing:order", order.ref)


@account_required
@pdf_view
def invoice(request, number):
    document = get_object_or_404(Invoice, number=number, account=request.account)
    audit("invoice.downloaded", actor=request.user, account=request.account, obj=document.number)
    response = HttpResponse(document.read(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{document.number}.pdf"'
    return response


@account_required
@require_POST
def cancel(request):
    live = services.subscriptions(request.account)
    chosen = request.POST.get("subscription")
    subscription = live.filter(pk=chosen).first() if chosen else services.active_subscription(request.account)
    if subscription and subscription.status in (Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE):
        services.cancel(subscription, request.user)
        messages.success(request, f"Cancelled. Your {subscription.plan} plan stays active until {subscription.period_end:%d %B %Y}.")
    return redirect("billing:overview")


@account_required
def provider_return(request, payment_id):
    """The customer is back from Stripe or PayPal: ask the provider right away (server to
    server) instead of waiting for the webhook, then show the order."""
    payment = get_object_or_404(Payment.objects.select_related("order"), pk=payment_id, order__account=request.account)
    if payment.status in OPEN:
        try:
            event = get_provider(payment.provider).confirm(payment)
            if event is not None:
                payments.apply_event(payment.provider, event, source=PaymentEvent.Source.CUSTOMER)
        except (ProviderError, WebhookRejected) as exc:
            log.warning("could not confirm %s: %s", payment, exc)  # the webhook will follow
    return redirect("billing:order", payment.order.ref)


@account_required
def sandbox_checkout(request, ref):
    """Stands in for a provider's hosted checkout page. No money moves."""
    order = get_object_or_404(Order, ref=ref, account=request.account)
    try:
        provider = get_provider("sandbox")
        if request.method == "POST" and order.status == Order.Status.PENDING:
            event_type = EVENT_PAID if request.POST.get("outcome") == "pay" else EVENT_FAILED
            body, headers = provider.build_event(order, event_type)
            # Same code path as a real webhook: signature check, stored once, applied once.
            payments.process_webhook("sandbox", body, headers)
            if event_type == EVENT_PAID:
                messages.success(request, f"Payment received. Your {order.plan} plan is active.")
            else:
                messages.error(request, "The payment failed. Nothing was changed.")
            return redirect("billing:order", order.ref)
    except WebhookRejected:
        raise Http404 from None
    return render(request, "billing/sandbox_checkout.html", {"order": order})


@csrf_exempt
@require_POST
def webhook(request, provider):
    """Provider callbacks. CSRF-exempt by design; authenticity comes from the signature."""
    try:
        outcome = payments.process_webhook(provider, request.body, request.headers)
    except WebhookRejected as exc:
        return HttpResponseBadRequest(str(exc))
    return HttpResponse(outcome, content_type="text/plain")
