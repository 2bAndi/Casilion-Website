from django.contrib import messages
from django.shortcuts import redirect, render

from apps.audit.log import audit
from apps.licensing.services import current_entitlement
from apps.support.models import Ticket

from .decorators import account_required
from .forms import AccountForm, AddressForm, TaxProfileForm
from .models import Address, TaxProfile


@account_required
def dashboard(request):
    account = request.account
    entitlement = current_entitlement(account)
    open_tickets = account.tickets.exclude(status__in=[Ticket.Status.CLOSED, Ticket.Status.REJECTED])
    return render(
        request,
        "accounts/dashboard.html",
        {
            "entitlement": entitlement,
            "license": entitlement.licenses.filter(revoked_at__isnull=True).first(),
            "open_tickets": open_tickets[:5],
            "open_ticket_count": open_tickets.count(),
            "agent_token_count": account.agent_tokens.filter(revoked_at__isnull=True).count(),
        },
    )


@account_required
def settings_view(request):
    account = request.account
    address, _ = Address.objects.get_or_create(account=account)
    tax, _ = TaxProfile.objects.get_or_create(account=account)
    forms = {
        "account_form": AccountForm(request.POST or None, instance=account, prefix="account"),
        "address_form": AddressForm(request.POST or None, instance=address, prefix="address"),
        "tax_form": TaxProfileForm(request.POST or None, instance=tax, prefix="tax"),
    }
    if request.method == "POST" and all(form.is_valid() for form in forms.values()):
        for form in forms.values():
            form.save()
        audit("account.updated", actor=request.user, account=account)
        messages.success(request, "Account details saved.")
        return redirect("accounts:settings")
    return render(request, "accounts/settings.html", forms)
