"""Every mail the site sends goes through here: it is delivered by the real backend
(``MAIL_DELIVERY_BACKEND``) and written to the mail log, so the backoffice can show a customer
everything that went out to them. Logging never stops a mail from being sent."""
import logging
import re

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.utils.module_loading import import_string

log = logging.getLogger(__name__)

# Confirmation and password-reset links let whoever reads them sign in. They are not stored.
AUTH_LINK = re.compile(r"https?://\S*/auth/\S+")
SKIP_HEADER = "X-Cassilion-Log"  # set to "skip" by code that keeps its own record (replies from the inbox)


def account_for_address(address: str):
    """The customer account a mail address belongs to: a login, or a billing address."""
    from apps.accounts.models import Membership, TaxProfile

    address = (address or "").strip().lower()
    if not address:
        return None
    membership = Membership.objects.filter(user__email__iexact=address).select_related("account").first()
    if membership:
        return membership.account
    tax = TaxProfile.objects.filter(billing_email__iexact=address).select_related("account").first()
    return tax.account if tax else None


def record(message, *, ok: bool, error: str = "") -> None:
    from .models import MailLog

    recipients = [*(message.to or []), *(message.cc or [])]
    account = next((a for a in map(account_for_address, recipients) if a is not None), None)
    body = AUTH_LINK.sub("[sign-in link, not stored]", str(message.body or ""))
    admins = {(a if isinstance(a, str) else a[1]).lower() for a in settings.ADMINS}
    if admins and str(message.subject).startswith(settings.EMAIL_SUBJECT_PREFIX) and {r.lower() for r in recipients} <= admins:
        # Django's error report: a traceback with request data. It goes to the operators' mailbox, not into the database.
        body = "[server error report, not stored]"
    names = []
    for attachment in message.attachments or []:
        name = attachment[0] if isinstance(attachment, tuple) else getattr(attachment, "get_filename", lambda: "")()
        names.append(str(name or "attachment")[:200])
    MailLog.objects.create(
        to=", ".join(recipients)[:500],
        subject=str(message.subject)[:300],
        body=body[:100_000],
        attachments=names,
        status=MailLog.Status.SENT if ok else MailLog.Status.FAILED,
        error=error[:500],
        account=account,
    )


class LoggingBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.inner = import_string(settings.MAIL_DELIVERY_BACKEND)(fail_silently=fail_silently, **kwargs)

    def open(self):
        return self.inner.open()

    def close(self):
        return self.inner.close()

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            skip = (getattr(message, "extra_headers", None) or {}).pop(SKIP_HEADER, "") == "skip"
            error, count, failure = "", 0, None
            try:
                count = self.inner.send_messages([message]) or 0
            except Exception as exc:  # noqa: BLE001 - logged below, raised again
                error, failure = f"{exc.__class__.__name__}: {exc}", exc
            if not skip:
                try:
                    record(message, ok=bool(count), error=error or ("" if count else "not accepted by the mail server"))
                except Exception:  # noqa: BLE001 - the log must never break sending
                    log.exception("mail log could not be written")
            if failure is not None:
                raise failure
            sent += count
        return sent
