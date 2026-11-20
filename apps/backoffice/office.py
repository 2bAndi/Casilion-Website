"""What used to need the raw-data screens: the audit log, releases, the team, and the
withdrawals and cancellations customers declared on the website."""
import json
from datetime import datetime, time, timedelta
from pathlib import Path

from allauth.mfa.models import Authenticator
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Account, User
from apps.audit.log import audit
from apps.audit.models import AuditEvent
from apps.billing.models import Plan, Subscription
from apps.core.models import ContractNotice
from apps.downloads import services as downloads
from apps.downloads.models import Artefact, Release
from apps.licensing import services as licensing
from apps.licensing.models import Entitlement
from apps.mailing.backend import account_for_address

from .views import owner_required, staff_required


# --------------------------------------------------------------------------- audit log

AREAS = [
    ("", "Everything"), ("payment", "Payment settings and matters"), ("billing", "Orders and plans"), ("invoice", "Invoices"),
    ("customer", "Customers"), ("account", "Customer accounts (by the customer)"), ("license", "Licenses"), ("product", "Products"),
    ("plan", "Plans"), ("mail", "Mail"), ("ticket", "Tickets"), ("support", "Support switches"), ("agent_token", "Agent tokens"),
    ("release", "Releases"), ("team", "Team"), ("notice", "Withdrawals and cancellations"), ("order", "Orders viewed"),
]


def _day(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


@staff_required
def audit_log(request):
    """Who did what, when, and on whose behalf. Read only: the log has no edit and no delete."""
    area, who, text = request.GET.get("area") or "", (request.GET.get("who") or "").strip(), (request.GET.get("q") or "").strip()
    since, until = _day(request.GET.get("from")), _day(request.GET.get("to"))
    rows = AuditEvent.objects.select_related("actor", "account")
    if area in dict(AREAS) and area:
        rows = rows.filter(action__startswith=area + ".")
    if who:
        rows = rows.filter(Q(actor__email__icontains=who) | Q(actor_kind__iexact=who))
    if text:
        rows = rows.filter(Q(object_ref__icontains=text) | Q(account__name__icontains=text) | Q(action__icontains=text))
    tz = timezone.get_current_timezone()
    if since:
        rows = rows.filter(at__gte=datetime.combine(since, time.min, tz))
    if until:
        rows = rows.filter(at__lt=datetime.combine(until + timedelta(days=1), time.min, tz))
    if request.GET.get("changes") == "1":  # leave out who merely looked at something
        rows = rows.exclude(action__endswith=".viewed").exclude(action__endswith=".downloaded")
    page = Paginator(rows, 100).get_page(request.GET.get("page"))
    for event in page:
        event.detail = json.dumps(event.data, ensure_ascii=False, sort_keys=True) if event.data else ""
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "backoffice/audit_log.html",
        {"page": page, "areas": AREAS, "area": area, "who": who, "q": text, "since": since, "until": until,
         "changes": request.GET.get("changes") == "1", "query": query.urlencode()},
    )


# --------------------------------------------------------------------------- releases


class ReleaseForm(forms.ModelForm):
    class Meta:
        model = Release
        fields = ["version", "channel", "notes", "is_published"]
        labels = {"is_published": "Published: customers see it under Downloads"}
        widgets = {"notes": forms.Textarea(attrs={"rows": 8})}


def _unregistered(release) -> list[str]:
    """Files that lie in the release's folder on the server and are not offered yet."""
    root = Path(settings.CASSILION["RELEASES_DIR"]).resolve()
    folder = (root / release.version).resolve()
    if root not in folder.parents or not folder.is_dir():
        return []
    known = set(release.artefacts.values_list("storage_path", flat=True))
    found = [str(p.relative_to(root)).replace("\\", "/") for p in sorted(folder.iterdir()) if p.is_file()]
    return [name for name in found if name not in known]


@staff_required
def releases(request):
    rows = Release.objects.annotate(fetched=Count("artefacts__downloads", distinct=True)).prefetch_related("artefacts").order_by("-id")
    return render(request, "backoffice/releases.html", {"releases": rows})


@staff_required
def release(request, pk=None):
    record = get_object_or_404(Release, pk=pk) if pk else None
    action = request.POST.get("action") if request.method == "POST" else ""
    form = ReleaseForm(request.POST if action == "save" else None, instance=record)
    if action == "save" and form.is_valid():
        was_published = bool(record and record.is_published)
        record = form.save(commit=False)
        if record.is_published and not record.published_at:
            record.published_at = timezone.now()
        record.save()
        audit("release.saved", actor=request.user, actor_kind="operator", obj=record.version,
              data={"fields": sorted(form.changed_data), "published": record.is_published, "was_published": was_published})
        messages.success(request, f"Release {record.version} saved.")
        return redirect("backoffice:release", record.pk)
    if record and action == "add_file":
        name, platform = request.POST.get("file") or "", (request.POST.get("platform") or "").strip()
        if name not in _unregistered(record) or not platform:
            messages.error(request, "Choose a file from the list and name its platform.")
        elif record.artefacts.filter(platform=platform).exists():
            messages.error(request, f"There is already a file for {platform}. Remove it first.")
        else:
            artefact = downloads.register_artefact(record, platform, Path(settings.CASSILION["RELEASES_DIR"]) / name)
            audit("release.file_added", actor=request.user, actor_kind="operator", obj=record.version,
                  data={"platform": platform, "file": artefact.filename, "sha256": artefact.sha256})
            messages.success(request, f"{artefact.filename} is offered for {platform}.")
        return redirect("backoffice:release", record.pk)
    if record and action == "remove_file":
        artefact = get_object_or_404(Artefact, pk=request.POST.get("artefact"), release=record)
        if artefact.downloads.exists():
            messages.error(request, "This file was downloaded by customers. It stays, so the list of who has it stays. Unpublish the release instead.")
        else:
            audit("release.file_removed", actor=request.user, actor_kind="operator", obj=record.version, data={"platform": artefact.platform, "file": artefact.filename})
            artefact.delete()  # the record only; the file stays on the server
            messages.success(request, "Removed from the release. The file itself is still on the server.")
        return redirect("backoffice:release", record.pk)
    return render(
        request,
        "backoffice/release.html",
        {
            "form": form,
            "release": record,
            "artefacts": record.artefacts.annotate(fetched=Count("downloads")) if record else [],
            "files": _unregistered(record) if record else [],
            "folder": f"{settings.CASSILION['RELEASES_DIR']}/{record.version}/" if record else "",
        },
    )


# --------------------------------------------------------------------------- a plan by hand, logins


class GrantForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=Plan.objects.select_related("product"), label="Plan")
    valid_until = forms.DateField(label="Valid until", required=False, widget=forms.DateInput(attrs={"type": "date"}),
                                  help_text="Empty = no end date. After this day the account is back on the free plan.")
    note = forms.CharField(label="Why (kept in the audit log)", max_length=300)

    def clean_valid_until(self):
        day = self.cleaned_data["valid_until"]
        if day and day < timezone.localdate():
            raise forms.ValidationError("This day is in the past.")
        return day


@staff_required
@require_POST
def grant_plan(request, pk):
    """Give a customer a plan without an order: a plan by quote, a goodwill extension, a partner."""
    account = get_object_or_404(Account, pk=pk)
    back = redirect(_customer_url(account) + "?tab=licenses")
    form = GrantForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(f"{form.fields[name].label}: {' '.join(errors)}" for name, errors in form.errors.items()))
        return back
    plan = form.cleaned_data["plan"]
    running = account.subscriptions.filter(status__in=Subscription.LIVE, plan__product=plan.product).first()
    if running:
        messages.error(request, f"{account.name} pays for {running.plan} until {running.period_end:%d %B %Y}. A plan by hand would be taken away again when that term ends: cancel or refund it first.")
        return back
    entitlement = licensing.grant(account, plan, source=Entitlement.Source.MANUAL, valid_until=form.cleaned_data["valid_until"])
    audit("license.granted_by_hand", actor=request.user, actor_kind="operator", account=account, obj=str(entitlement.pk),
          data={"plan": plan.code, "until": str(form.cleaned_data["valid_until"] or ""), "note": form.cleaned_data["note"]})
    messages.success(request, f"{account.name} is on {plan} now. A new license file has been issued.")
    return back


def _customer_url(account) -> str:
    from django.urls import reverse

    return reverse("backoffice:customer", args=[account.pk])


@staff_required
@require_POST
def end_plan(request, pk):
    """Take a plan that was given by hand away again: back to the product's free plan."""
    account = get_object_or_404(Account, pk=pk)
    entitlement = get_object_or_404(Entitlement, pk=request.POST.get("entitlement"), account=account, is_active=True, source=Entitlement.Source.MANUAL)
    licensing.grant_free(account, entitlement.product)
    audit("license.ended_by_hand", actor=request.user, actor_kind="operator", account=account, obj=str(entitlement.pk), data={"plan": entitlement.plan.code})
    messages.success(request, f"{entitlement.plan} ended. The account is on the free plan of {entitlement.product.name}.")
    return redirect(_customer_url(account) + "?tab=licenses")


@staff_required
@require_POST
def login_block(request, pk):
    """Block or unblock one login of a customer. A blocked login cannot sign in, and its agent tokens stop working."""
    account = get_object_or_404(Account, pk=pk)
    user = get_object_or_404(User, pk=request.POST.get("user"), memberships__account=account)
    if user.is_staff:
        messages.error(request, "This login belongs to the team. Change it under Team.")
    else:
        user.is_active = request.POST.get("action") == "unblock"
        user.save(update_fields=["is_active"])
        audit("customer.login_unblocked" if user.is_active else "customer.login_blocked", actor=request.user, actor_kind="operator", account=account, obj=user.email)
        messages.success(request, f"{user.email} can sign in again." if user.is_active else f"{user.email} is blocked.")
    return redirect(_customer_url(account))


# --------------------------------------------------------------------------- team


@staff_required
def team(request):
    people = list(User.objects.filter(is_staff=True).order_by("email"))
    with_2fa = set(Authenticator.objects.filter(user__in=people).values_list("user_id", flat=True))
    for person in people:
        person.two_factor = person.pk in with_2fa
    return render(request, "backoffice/team.html", {"people": people, "owner": request.user.is_superuser})


@owner_required
@require_POST
def team_change(request):
    action, me = request.POST.get("action"), request.user
    if action == "add":
        email = (request.POST.get("email") or "").strip()
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            messages.error(request, "Nobody has registered with this address. The person registers on the site first, then you add them here.")
        elif user.is_staff:
            messages.error(request, f"{user.email} is in the team already.")
        else:
            user.is_staff = True
            user.save(update_fields=["is_staff"])
            audit("team.added", actor=me, actor_kind="operator", obj=user.email)
            messages.success(request, f"{user.email} can use the backoffice now. Ask them to switch on two-factor sign-in.")
        return redirect("backoffice:team")
    user = get_object_or_404(User, pk=request.POST.get("user"), is_staff=True)
    if user == me:
        messages.error(request, "You cannot change your own access. Ask another owner.")
    elif action == "remove":
        user.is_staff = user.is_superuser = False
        user.save(update_fields=["is_staff", "is_superuser"])
        audit("team.removed", actor=me, actor_kind="operator", obj=user.email)
        messages.success(request, f"{user.email} is out of the team. The login itself still exists.")
    elif action in ("block", "unblock"):
        user.is_active = action == "unblock"
        user.save(update_fields=["is_active"])
        audit(f"team.{action}ed", actor=me, actor_kind="operator", obj=user.email)
        messages.success(request, f"{user.email} {'can sign in again' if user.is_active else 'is blocked'}.")
    elif action in ("make_owner", "unmake_owner"):
        user.is_superuser = action == "make_owner"
        user.save(update_fields=["is_superuser"])
        audit(f"team.{action}", actor=me, actor_kind="operator", obj=user.email)
        messages.success(request, f"{user.email} {'is an owner now' if user.is_superuser else 'is an operator now'}.")
    return redirect("backoffice:team")


# --------------------------------------------------------------------------- withdrawals, cancellations


@staff_required
def notices(request):
    show = request.GET.get("show") or "open"
    rows = ContractNotice.objects.all()
    if show == "open":
        rows = rows.filter(handled_at__isnull=True)
    elif show == "done":
        rows = rows.filter(handled_at__isnull=False)
    return render(request, "backoffice/notices.html", {"notices": rows[:200], "show": show})


@staff_required
def notice(request, reference):
    record = get_object_or_404(ContractNotice, reference=reference)
    if request.method == "POST":
        # The declaration stays as it arrived. Only what the team did about it is written.
        record.handling_note = (request.POST.get("note") or "").strip()
        if request.POST.get("action") == "done":
            record.handled_at = record.handled_at or timezone.now()
        elif request.POST.get("action") == "reopen":
            record.handled_at = None
        record.save(update_fields=["handling_note", "handled_at"])
        audit("notice.handled" if record.handled_at else "notice.noted", actor=request.user, actor_kind="operator", obj=record.reference)
        messages.success(request, "Saved.")
        return redirect("backoffice:notice", record.reference)
    account = account_for_address(record.email)
    return render(request, "backoffice/notice.html", {"notice": record, "account": account})
