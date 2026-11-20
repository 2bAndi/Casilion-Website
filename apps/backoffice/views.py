"""The operators' side: review queue, tickets with the AI's analysis, sent log, switches."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Account
from apps.audit.log import audit
from apps.billing.models import Order, ReconciliationIssue, Subscription
from apps.core.models import ContractNotice
from apps.mcp_server import tokens
from apps.mcp_server.models import AgentToken
from apps.support import services
from apps.support.models import AIJob, Message, Ticket
from shared.schemas import ANSWER_CLASSES

from .models import SupportSettings

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="account_login")


def status_snapshot() -> dict:
    """Shared by the dashboard and the operator MCP tool site_status."""
    limits = SupportSettings.load()
    now = timezone.now()
    seen = limits.worker_last_seen
    month = now - timedelta(days=30)
    paid = Order.objects.filter(status=Order.Status.PAID, paid_at__gte=month, is_test=False)
    return {
        "review_queue": Ticket.objects.filter(status=Ticket.Status.IN_REVIEW).count(),
        "bug_candidates": Ticket.objects.filter(status=Ticket.Status.BUG_CANDIDATE).count(),
        "open_tickets": Ticket.objects.filter(status__in=Ticket.OPEN_STATES).count(),
        "critical_open": Ticket.objects.filter(status__in=Ticket.OPEN_STATES, priority=Ticket.Priority.CRITICAL).count(),
        "jobs_waiting": AIJob.objects.filter(status=AIJob.Status.QUEUED).count(),
        "auto_send": limits.auto_send,
        "ai_worker_last_seen": seen.isoformat(timespec="seconds") if seen else None,
        "ai_worker_online": bool(seen and now - seen < timedelta(minutes=2)),
        "accounts": Account.objects.count(),
        "active_subscriptions": Subscription.objects.filter(status=Subscription.Status.ACTIVE).count(),
        "paid_orders_30d": paid.count(),
        "revenue_eur_30d": float(paid.aggregate(sum=Sum("total"))["sum"] or 0),
        "payment_issues": ReconciliationIssue.objects.filter(resolved=False).count(),
        "open_notices": ContractNotice.objects.filter(handled_at__isnull=True).count(),
    }


@staff_required
def dashboard(request):
    plans = (
        Subscription.objects.filter(status=Subscription.Status.ACTIVE)
        .values("plan__product__name", "plan__name")
        .annotate(n=Count("id"))
        .order_by("plan__product__rank", "plan__rank")
    )
    return render(
        request,
        "backoffice/dashboard.html",
        {
            "status": status_snapshot(),
            "queue": services.review_queue()[:8],
            "plans": plans,
            "limits": SupportSettings.load(),
        },
    )


@staff_required
def review_queue(request):
    return render(request, "backoffice/review_queue.html", {"tickets": services.review_queue()})


@staff_required
def tickets(request):
    status = request.GET.get("status") or ""
    queryset = Ticket.objects.select_related("account")
    if status in Ticket.Status.values:
        queryset = queryset.filter(status=status)
    return render(
        request,
        "backoffice/tickets.html",
        {"tickets": queryset[:200], "statuses": Ticket.Status.choices, "current": status},
    )


@staff_required
def ticket(request, public_id):
    ticket = get_object_or_404(Ticket.objects.select_related("account", "author"), public_id=public_id)
    if request.method == "POST":
        return _ticket_action(request, ticket)
    audit("ticket.viewed", actor=request.user, actor_kind="operator", account=ticket.account, obj=ticket.public_id)
    run = ticket.runs.select_related("finding").first()
    return render(
        request,
        "backoffice/ticket.html",
        {
            "ticket": ticket,
            "thread": ticket.messages.exclude(state=Message.State.DISCARDED),
            "draft": ticket.messages.filter(state=Message.State.DRAFT).last(),
            "run": run,
            "finding": getattr(run, "finding", None) if run else None,
            "verdicts": ticket.verdicts.all(),
            "events": ticket.events.select_related("actor"),
            "blocked": bool(run and run.verdicts.filter(verdict="block").exists()),
        },
    )


def _ticket_action(request, ticket):
    action, user = request.POST.get("action"), request.user
    try:
        if action in {"approve", "reject"}:
            draft = get_object_or_404(Message, pk=request.POST.get("draft"), ticket=ticket)
            if action == "approve":
                services.approve_draft(draft, user, request.POST.get("body"))
                messages.success(request, "Reply sent to the customer.")
            else:
                services.reject_draft(draft, user, request.POST.get("note", ""))
                messages.success(request, "Draft rejected. Write the reply yourself below.")
        elif action == "reply":
            internal = request.POST.get("internal") == "on"
            services.operator_reply(ticket, user, request.POST.get("body", ""), internal=internal)
            messages.success(request, "Internal note added." if internal else "Reply sent to the customer.")
        elif action == "confirm_bug":
            services.confirm_bug(ticket, user, request.POST.get("issue_url", ""))
            messages.success(request, "Marked as a confirmed product bug.")
        elif action == "dismiss_bug":
            services.dismiss_bug(ticket, user)
            messages.success(request, "Not a bug. The ticket is back in review.")
        elif action == "close":
            services.close_ticket(ticket, user, actor_kind="operator")
            messages.success(request, "Ticket closed.")
    except services.SupportError as exc:
        messages.error(request, str(exc))
    return redirect("backoffice:ticket", ticket.public_id)


@staff_required
def sent_log(request):
    sent = (
        Message.objects.filter(visibility=Message.Visibility.PUBLIC, state=Message.State.SENT)
        .exclude(author_kind__in=[Message.Author.CUSTOMER, Message.Author.CUSTOMER_AGENT])
        .select_related("ticket", "ticket__account", "review", "review__reviewer")
        .order_by("-sent_at")[:200]
    )
    return render(request, "backoffice/sent_log.html", {"sent": sent})


class SettingsForm(forms.ModelForm):
    auto_send_classes = forms.MultipleChoiceField(
        label="Classes that may be sent automatically",
        choices=[(c, c.replace("_", " ")) for c in ANSWER_CLASSES],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = SupportSettings
        fields = ["auto_send", "auto_send_classes", "max_rounds", "max_tokens_per_ticket", "stale_days"]
        labels = {"auto_send": "Send confident answers automatically"}


@staff_required
def settings_view(request):
    limits = SupportSettings.load()
    form = SettingsForm(request.POST or None, instance=limits)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit("support.settings_changed", actor=request.user, actor_kind="operator", data={"auto_send": limits.auto_send})
        messages.success(request, "Settings saved.")
        return redirect("backoffice:settings")
    return render(
        request,
        "backoffice/settings.html",
        {
            "form": form,
            "tokens": AgentToken.objects.filter(kind=AgentToken.Kind.OPERATOR, user=request.user, revoked_at__isnull=True),
            "new_token": request.session.pop("new_operator_token", None),
        },
    )


@staff_required
@require_POST
def operator_token_create(request):
    _record, plaintext = tokens.issue(request.user, kind=AgentToken.Kind.OPERATOR, name=request.POST.get("name", ""))
    request.session["new_operator_token"] = plaintext
    return redirect("backoffice:settings")


@staff_required
@require_POST
def operator_token_revoke(request, token_id):
    tokens.revoke(get_object_or_404(AgentToken, pk=token_id, kind=AgentToken.Kind.OPERATOR, user=request.user), request.user)
    return redirect("backoffice:settings")
