"""The mailbox customers write to, in the backoffice: read, reply, put with a customer."""
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.accounts.models import Account
from apps.audit.log import audit
from apps.mailing import inbox as mailbox
from apps.mailing.backend import account_for_address
from apps.mailing.imap import MailboxError, open_mailbox
from apps.mailing.models import Conversation, MailboxSettings

from .views import owner_required, staff_required

FILTERS = [("new", "To answer"), ("answered", "Answered"), ("done", "Done"), ("automatic", "Automatic mails"), ("all", "All")]


@staff_required
def inbox(request):
    current = request.GET.get("show") or "new"
    rows = Conversation.objects.select_related("account")
    if current == "automatic":
        rows = rows.filter(is_automatic=True)
    elif current in Conversation.Status.values:
        rows = rows.filter(status=current, is_automatic=False)
    config = MailboxSettings.load()
    return render(request, "backoffice/inbox.html", {"conversations": rows[:200], "filters": FILTERS, "current": current, "config": config})


@staff_required
def conversation(request, pk):
    thread = get_object_or_404(Conversation.objects.select_related("account"), pk=pk)
    if request.method == "POST":
        return _action(request, thread)
    if not thread.is_read:
        thread.is_read = True
        thread.save(update_fields=["is_read"])
    audit("mail.viewed", actor=request.user, actor_kind="operator", account=thread.account, obj=str(thread.pk))
    return render(
        request,
        "backoffice/conversation.html",
        {"thread": thread, "mails": thread.messages.select_related("sent_by"), "sender": mailbox.sender_address()},
    )


def _action(request, thread):
    action, user = request.POST.get("action"), request.user
    if action == "reply":
        body = (request.POST.get("body") or "").strip()
        if not body:
            messages.error(request, "Write the reply first.")
        else:
            try:
                mailbox.send_reply(thread, body, user=user)
                audit("mail.replied", actor=user, actor_kind="operator", account=thread.account, obj=str(thread.pk))
                messages.success(request, f"Reply sent to {thread.contact_email}.")
            except Exception as exc:  # noqa: BLE001 - whatever the mail server says goes to the operator
                messages.error(request, f"The reply was not sent: {exc}")
    elif action in ("done", "reopen"):
        thread.status = Conversation.Status.DONE if action == "done" else Conversation.Status.NEW
        thread.save(update_fields=["status"])
    elif action == "assign":
        value = (request.POST.get("customer") or "").strip()
        account = account_for_address(value) or Account.objects.filter(name__iexact=value).first()
        if account is None:
            messages.error(request, "No customer with this login, billing address or exact name.")
        else:
            thread.account = account
            thread.save(update_fields=["account"])
            audit("mail.assigned", actor=user, actor_kind="operator", account=account, obj=str(thread.pk))
            messages.success(request, f"Put with {account.name}.")
    elif action == "unassign":
        thread.account = None
        thread.save(update_fields=["account"])
    return redirect("backoffice:conversation", thread.pk)


class MailboxForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Password", required=False, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
    )
    clear_password = forms.BooleanField(label="Remove the stored password", required=False)

    class Meta:
        model = MailboxSettings
        fields = ["enabled", "host", "port", "user", "initial_days"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stored = bool(self.instance.password)
        self.fields["new_password"].help_text = (
            "Stored. Leave empty to keep it; enter a new value to replace it." if stored
            else "Not set. With an empty user name the password of the sending account is used."
        )
        if not stored:
            del self.fields["clear_password"]
        self.order_fields(["enabled", "host", "port", "user", "new_password", "clear_password", "initial_days"])

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("clear_password"):
            obj.password = ""
        elif self.cleaned_data.get("new_password"):
            obj.password = self.cleaned_data["new_password"]
        obj.save()
        return obj


@sensitive_post_parameters()
@owner_required
def inbox_settings(request):
    config = MailboxSettings.load()
    form = MailboxForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        changed = sorted(n for n in form.changed_data if n not in ("new_password", "clear_password") or form.cleaned_data.get(n))
        form.save()
        audit("mail.settings_changed", actor=request.user, actor_kind="operator", data={"fields": changed})
        messages.success(request, "Mailbox settings saved.")
        return redirect("backoffice:inbox_settings")
    return render(request, "backoffice/inbox_settings.html", {"form": form, "config": config})


@staff_required
@require_POST
def inbox_check(request):
    """Try the mailbox as saved, or fetch now instead of waiting for the next run."""
    config = MailboxSettings.load()
    if request.POST.get("action") == "fetch":
        added = mailbox.fetch(config)
        if config.last_error:
            messages.error(request, config.last_error)
        else:
            messages.success(request, f"{added} new mail(s) read.")
        return redirect("backoffice:inbox")
    try:
        with open_mailbox(config) as box:
            box.select("INBOX", readonly=True)
        messages.success(request, f"Connection works: {config.effective_user} at {config.effective_host}.")
    except (MailboxError, OSError) as exc:
        messages.error(request, str(exc))
    return redirect("backoffice:inbox_settings")
