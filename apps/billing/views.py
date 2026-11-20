from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.decorators import account_required
from apps.accounts.models import Account
from apps.licensing.services import current_entitlement

from . import services
from .models import Order, Plan
from .providers import WebhookError, get_provider
from .providers.base import EVENT_FAILED, EVENT_PAID


def pricing(request):
    current = current_entitlement(request.account).plan if request.user.is_authenticated and request.account else None
    return render(request, "billing/pricing.html", {"plans": services.public_plans(), "current_plan": current})


@account_required
def overview(request):
    account = request.account
    return render(
        request,
        "billing/overview.html",
        {
            "entitlement": current_entitlement(account),
            "subscription": services.active_subscription(account),
            "orders": account.orders.select_related("plan")[:20],
            "plans": services.public_plans(),
        },
    )


@account_required
def checkout_start(request, code):
    """The order page: what is ordered, what it costs, how it ends; then the binding order button."""
    plan = get_object_or_404(Plan, code=code, is_public=True)
    account = request.account
    if plan.price_eur_year <= 0 or not plan.self_service:
        messages.error(request, f"{plan.name} cannot be bought in the shop.")
        return redirect("billing:pricing")
    # Only consumers have a right of withdrawal, so only they are asked about an early start.
    is_consumer = account.kind == Account.Kind.INDIVIDUAL
    error = ""
    if request.method == "POST":
        early_start = request.POST.get("early_start") == "on"
        if is_consumer and not early_start:
            error = "Please confirm that support should start right away. Without this we cannot activate the plan before the withdrawal period has ended."
        else:
            try:
                _order, url = services.start_checkout(account, request.user, plan, request, early_start=is_consumer and early_start)
            except services.CheckoutError as exc:
                messages.error(request, str(exc))
                return redirect("billing:pricing")
            return redirect(url)
    return render(request, "billing/checkout.html", {"plan": plan, "is_consumer": is_consumer, "error": error})


@account_required
@require_POST
def cancel(request):
    subscription = services.active_subscription(request.account)
    if subscription:
        services.cancel(subscription, request.user)
        messages.success(request, f"Cancelled. Your plan stays active until {subscription.period_end:%d %B %Y}.")
    return redirect("billing:overview")


@account_required
def sandbox_checkout(request, ref):
    """Stands in for the provider's hosted checkout page. Local development only."""
    order = get_object_or_404(Order, ref=ref, account=request.account)
    if request.method == "POST" and order.status == Order.Status.PENDING:
        provider = get_provider("sandbox")
        event_type = EVENT_PAID if request.POST.get("outcome") == "pay" else EVENT_FAILED
        body, headers = provider.build_event(order, event_type)
        # Same code path as a real webhook: signature check, stored once, applied once.
        services.handle_webhook("sandbox", body, headers)
        if event_type == EVENT_PAID:
            messages.success(request, f"Payment received. Your {order.plan.name} plan is active.")
        else:
            messages.error(request, "The payment failed. Nothing was changed.")
        return redirect("billing:overview")
    return render(request, "billing/sandbox_checkout.html", {"order": order})


@csrf_exempt
@require_POST
def webhook(request, provider):
    try:
        services.handle_webhook(provider, request.body, request.headers)
    except WebhookError as exc:
        return HttpResponseBadRequest(str(exc))
    return HttpResponse("ok")
