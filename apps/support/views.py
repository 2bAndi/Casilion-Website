from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import account_required
from apps.licensing.services import current_entitlement

from . import services
from .forms import ReplyForm, TicketForm
from .models import Message, Ticket


def _ticket(request, public_id) -> Ticket:
    return get_object_or_404(Ticket, public_id=public_id, account=request.account)


@account_required
def ticket_list(request):
    plan = current_entitlement(request.account).plan
    return render(request, "support/ticket_list.html", {"tickets": request.account.tickets.all()[:100], "plan": plan})


@account_required
def ticket_new(request):
    plan = current_entitlement(request.account).plan
    bug_report = not plan.has_support
    form = TicketForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            ticket = services.open_ticket(
                request.account,
                request.user,
                channel=Ticket.Channel.WEB,
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                product_version=form.cleaned_data["product_version"],
                diagnostics=form.cleaned_data["diagnostics"],
                bug_report=bug_report,
            )
        except services.SupportError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Bug report recorded." if bug_report else "Ticket opened. The support AI is looking at it.")
            return redirect("support:detail", ticket.public_id)
    return render(request, "support/ticket_new.html", {"form": form, "plan": plan, "bug_report": bug_report})


@account_required
def ticket_detail(request, public_id):
    ticket = _ticket(request, public_id)
    form = ReplyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.add_customer_message(
                ticket,
                request.user,
                channel=Ticket.Channel.WEB,
                body=form.cleaned_data["body"],
                diagnostics=form.cleaned_data["diagnostics"],
            )
        except services.SupportError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Reply added.")
            return redirect("support:detail", ticket.public_id)
    plan = current_entitlement(request.account).plan
    return render(
        request,
        "support/ticket_detail.html",
        {
            "ticket": ticket,
            "view": services.customer_view(ticket),
            "thread": ticket.messages.filter(visibility=Message.Visibility.PUBLIC, state=Message.State.SENT),
            "form": form,
            "can_reply": ticket.is_open and not ticket.bug_report,
            "can_ask_person": plan.human_on_request and ticket.is_open and not ticket.needs_human and not ticket.bug_report,
        },
    )


@account_required
@require_POST
def ticket_human(request, public_id):
    ticket = _ticket(request, public_id)
    try:
        services.request_human(ticket, request.user)
    except services.SupportError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "A member of the team has been asked to look at this ticket.")
    return redirect("support:detail", ticket.public_id)


@account_required
@require_POST
def ticket_close(request, public_id):
    ticket = _ticket(request, public_id)
    services.close_ticket(ticket, request.user)
    messages.success(request, "Ticket closed.")
    return redirect("support:list")
