"""Customers in the backoffice: find an account, see everything about it in one place, keep its data."""
from allauth.mfa.models import Authenticator
from django import forms
from django.contrib import messages
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.models import Account, Address, TaxProfile
from apps.audit.log import audit
from apps.billing.models import Order
from apps.licensing import services as licensing
from apps.licensing.models import License
from apps.mailing import history
from apps.mailing.models import Conversation
from apps.support.models import Message, Ticket

from .views import staff_required

WAITING = [Ticket.Status.IN_REVIEW, Ticket.Status.BUG_CANDIDATE]  # a person at Cassilion has to act
TABS = [("overview", "Master data"), ("mails", "Mails"), ("tickets", "Tickets"), ("orders", "Orders"), ("licenses", "Plans and licenses")]


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "kind", "country", "status"]


class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        fields = ["line1", "line2", "postal_code", "city", "region"]


class TaxForm(forms.ModelForm):
    class Meta:
        model = TaxProfile
        fields = ["vat_id", "billing_email"]


@staff_required
def customers(request):
    query = (request.GET.get("q") or "").strip()
    rows = Account.objects.all()
    if query:
        rows = rows.filter(
            Q(name__icontains=query) | Q(members__email__icontains=query) | Q(members__full_name__icontains=query)
            | Q(orders__ref__iexact=query) | Q(invoices__number__iexact=query) | Q(tax_profile__billing_email__icontains=query)
        ).distinct()
    rows = rows.annotate(
        last_login=Max("members__last_login"),
        waiting=Count("tickets", filter=Q(tickets__status__in=WAITING), distinct=True),
        to_answer=Count("conversations", filter=Q(conversations__status=Conversation.Status.NEW), distinct=True),
    ).prefetch_related("members", "entitlements__plan__product").order_by("name")[:300]
    listed = []
    for account in rows:
        plans = [e.plan for e in account.entitlements.all() if e.is_active]
        listed.append({"account": account, "plans": plans, "login": next(iter(account.members.all()), None)})
    return render(request, "backoffice/customers.html", {"rows": listed, "q": query, "total": Account.objects.count()})


def _figures(account) -> dict:
    members = list(account.memberships.select_related("user"))
    seen = [m.user for m in members if m.user.last_login]
    last = max(seen, key=lambda u: u.last_login) if seen else None
    from apps.core.models import ContractNotice

    emails = [m.user.email for m in members]
    return {
        "notices": ContractNotice.objects.filter(email__in=emails, handled_at__isnull=True),
        "last_login": last.last_login if last else None,
        "last_login_by": last.email if last else "",
        "plans": licensing.entitlements(account),
        "waiting": account.tickets.filter(status__in=WAITING).count(),
        "to_answer": account.conversations.filter(status=Conversation.Status.NEW).count(),
        "open_orders": account.orders.filter(status=Order.Status.PENDING).count(),
        "members": members,
    }


@staff_required
def customer(request, pk):
    account = get_object_or_404(Account, pk=pk)
    tab = request.GET.get("tab")
    tab = tab if tab in dict(TABS) else "overview"
    address, _ = Address.objects.get_or_create(account=account)
    tax, _ = TaxProfile.objects.get_or_create(account=account)
    data = request.POST if request.method == "POST" else None
    forms_ = {
        "account_form": AccountForm(data, instance=account, prefix="account"),
        "address_form": AddressForm(data, instance=address, prefix="address"),
        "tax_form": TaxForm(data, instance=tax, prefix="tax"),
    }
    if data is not None:
        tab = "overview"
        if all(form.is_valid() for form in forms_.values()):
            changed = sorted(f"{form.prefix}.{name}" for form in forms_.values() for name in form.changed_data)
            for form in forms_.values():
                form.save()
            # Who changed which fields of the customer; the values are in the database.
            audit("customer.updated", actor=request.user, actor_kind="operator", account=account, data={"fields": changed})
            messages.success(request, "Customer saved." if changed else "Nothing was changed.")
            return redirect(reverse("backoffice:customer", args=[account.pk]))
    else:
        audit("customer.viewed", actor=request.user, actor_kind="operator", account=account, data={"tab": tab})

    context = {"account": account, "tab": tab, "tabs": TABS, "figures": _figures(account), **forms_}
    if tab == "overview":
        users = [m.user_id for m in context["figures"]["members"]]
        context["two_factor"] = set(Authenticator.objects.filter(user_id__in=users).values_list("user_id", flat=True))
        context["tokens"] = account.agent_tokens.filter(revoked_at__isnull=True).select_related("user")
    elif tab == "mails":
        context["mails"] = history.for_account(account)
        context["addresses"] = history.addresses(account)
    elif tab == "tickets":
        tickets = list(account.tickets.prefetch_related("messages").order_by("-created_at")[:100])
        for ticket in tickets:
            thread = [m for m in ticket.messages.all() if m.visibility == Message.Visibility.PUBLIC]
            ticket.asked = [m for m in thread if m.from_customer]
            ticket.sent = [m for m in thread if not m.from_customer and m.state == Message.State.SENT]
            ticket.held = [m for m in thread if not m.from_customer and m.state == Message.State.DRAFT]
        context["tickets"] = tickets
        context["waiting"] = [t for t in tickets if t.status in WAITING or t.held]
    elif tab == "orders":
        context["orders"] = account.orders.prefetch_related("lines__plan__product")[:100]
        context["invoices"] = account.invoices.all()[:100]
    elif tab == "licenses":
        context["subscriptions"] = account.subscriptions.select_related("plan__product")
        context["entitlements"] = account.entitlements.select_related("plan__product").order_by("-is_active", "-created_at")
        from .office import GrantForm

        context["grant_form"] = GrantForm()
        context["licenses"] = License.objects.filter(entitlement__account=account).select_related("entitlement__plan__product")[:50]
    return render(request, "backoffice/customer.html", context)
