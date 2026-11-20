"""Every mail with a customer in one list: what the site sent (mail log) and the
conversations of the inbox, newest first. Used on the customer page of the backoffice."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Q

from .models import Conversation, InboxMessage, MailLog


@dataclass
class Entry:
    when: datetime
    direction: str  # "in" (from the customer) or "out" (from us)
    system: bool  # sent automatically by the site
    subject: str
    body: str = ""
    sender: str = ""
    to: str = ""
    failed: bool = False
    error: str = ""
    conversation_id: int | None = None
    attachments: list[str] = field(default_factory=list)


def addresses(account) -> list[str]:
    """Every mail address that belongs to the account: its logins and its billing address."""
    found = {u.email.lower() for u in account.members.all() if u.email}
    billing = getattr(getattr(account, "tax_profile", None), "billing_email", "")
    if billing:
        found.add(billing.lower())
    return sorted(found)


def for_account(account, limit: int = 300) -> list[Entry]:
    emails = addresses(account)
    by_address = Q()
    for address in emails:
        by_address |= Q(to__icontains=address)
    logs = MailLog.objects.filter(Q(account=account) | by_address) if emails else MailLog.objects.filter(account=account)
    conversations = Conversation.objects.filter(Q(account=account) | Q(contact_email__in=emails))
    messages = InboxMessage.objects.filter(conversation__in=conversations).select_related("sent_by")
    entries = [
        Entry(
            when=log.created_at, direction="out", system=True, subject=log.subject, body=log.body, to=log.to,
            failed=log.status == MailLog.Status.FAILED, error=log.error, attachments=list(log.attachments or []),
        )
        for log in logs[:limit]
    ]
    for m in messages.order_by("-sent_at")[:limit]:
        incoming = m.direction == InboxMessage.Direction.IN
        sender = (m.from_name or m.from_email) if incoming else (m.sent_by.get_full_name() if m.sent_by else m.from_name)
        entries.append(
            Entry(
                when=m.sent_at, direction="in" if incoming else "out", system=False, subject=m.subject, body=m.body, sender=sender,
                to=m.to, conversation_id=m.conversation_id, attachments=[a.get("name", "") for a in m.attachments or []],
            )
        )
    return sorted(entries, key=lambda e: e.when, reverse=True)[:limit]
