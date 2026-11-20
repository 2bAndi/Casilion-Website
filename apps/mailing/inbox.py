"""The inbox: mails to the company's address in the backoffice.

``fetch`` (every few minutes, ``manage.py fetch_inbox``) reads new mails over IMAP without
changing the mailbox, threads them into conversations, and matches each conversation to a
customer account: by an order or ticket number in the text, else by the sender's address. A
reply from the backoffice goes out with ``In-Reply-To``/``References``, so it lands in the
customer's thread.

Taken over from the Tangosummer ticket shop. Mail text is somebody else's text: it is stored
and shown as plain text, never as HTML, and nothing in it is acted upon.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import formataddr, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from .backend import SKIP_HEADER, account_for_address
from .imap import MailboxError, open_mailbox
from .models import Conversation, InboxMessage, MailboxSettings, MailLog

MAX_PER_RUN = 200
MAX_BODY = 100_000
ORDER_REF = re.compile(r"\bCO-[A-HJ-NP-Z2-9]{7}\b")
TICKET_ID = re.compile(r"\bT-[A-Z0-9]{6}\b")
SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|aw|antw|fwd?|wg|sv|vs|tr)\s*(?:\[\d+\])?\s*:\s*)+", re.I)
AUTOMATIC_SENDERS = {"mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply", "do-not-reply"}


# --------------------------------------------------------------------------- parsing


class _Text(HTMLParser):
    """The readable text of an HTML mail: no tags, no scripts, no styles; links keep their address."""

    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.hidden += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self.href = href if href.startswith(("http://", "https://", "mailto:")) else ""

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.hidden = max(self.hidden - 1, 0)
        elif tag == "a" and getattr(self, "href", ""):
            self.parts.append(f" <{self.href}>")
            self.href = ""
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - broken markup: keep what was read
        pass
    text = "".join(parser.parts)
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


@dataclass
class Parsed:
    message_id: str
    in_reply_to: str = ""
    references: str = ""
    from_email: str = ""
    from_name: str = ""
    reply_to: str = ""
    to: str = ""
    cc: str = ""
    subject: str = ""
    date: datetime = field(default_factory=timezone.now)
    body: str = ""
    attachments: list[dict] = field(default_factory=list)
    automatic: bool = False


def _header(msg, name: str) -> str:
    try:
        value = msg.get(name)
    except Exception:  # noqa: BLE001 - malformed header
        return ""
    return " ".join(str(value).split()) if value is not None else ""


def _text_of(part) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError, AssertionError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if not isinstance(content, str):
        content = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else ""
    if part.get_content_type() == "text/html":
        content = html_to_text(content)
    content = content.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return re.sub(r"\n{3,}", "\n\n", content).strip()[:MAX_BODY]


def parse(raw: bytes) -> Parsed:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    from_name, from_email = parseaddr(_header(msg, "From"))
    _, reply_to = parseaddr(_header(msg, "Reply-To"))
    try:
        date = parsedate_to_datetime(_header(msg, "Date"))
        if timezone.is_naive(date):
            date = timezone.make_aware(date, timezone.get_current_timezone())
    except (TypeError, ValueError, IndexError):
        date = timezone.now()
    date = min(date, timezone.now())  # a sender's clock in the future does not reorder the inbox
    body_part = msg.get_body(preferencelist=("plain", "html"))
    files = []
    for index, part in enumerate(msg.iter_attachments() if msg.is_multipart() else []):
        payload = part.get_payload(decode=True) or b""
        files.append({"index": index, "name": (part.get_filename() or "attachment")[:200], "type": part.get_content_type(), "size": len(payload)})
    local = from_email.split("@")[0].lower()
    automatic = (
        _header(msg, "Auto-Submitted").lower() not in ("", "no")
        or _header(msg, "Precedence").lower() in ("bulk", "list", "junk", "auto_reply")
        or bool(_header(msg, "List-Id") or _header(msg, "List-Unsubscribe"))
        or bool(_header(msg, "X-Autoreply") or _header(msg, "X-Autorespond"))
        or local in AUTOMATIC_SENDERS
    )
    message_id = _header(msg, "Message-ID") or make_msgid(domain="inbox.invalid")
    return Parsed(
        message_id=message_id[:300],
        in_reply_to=_header(msg, "In-Reply-To")[:300],
        references=_header(msg, "References")[:5000],
        from_email=from_email.lower()[:254],
        from_name=from_name[:200],
        reply_to=reply_to.lower()[:254],
        to=", ".join(a for _, a in getaddresses([_header(msg, "To")]) if a)[:500],
        cc=", ".join(a for _, a in getaddresses([_header(msg, "Cc")]) if a)[:500],
        subject=_header(msg, "Subject")[:300],
        date=date,
        body=_text_of(body_part) if body_part is not None else "",
        attachments=files,
        automatic=automatic,
    )


# --------------------------------------------------------------------------- threads and matching


def normalize_subject(subject: str) -> str:
    return " ".join(SUBJECT_PREFIX.sub("", subject or "").split()).lower()


def find_conversation(parsed: Parsed) -> Conversation | None:
    ids = [m for m in (parsed.in_reply_to, *parsed.references.split()) if m]
    known = InboxMessage.objects.filter(message_id__in=ids).select_related("conversation").order_by("-sent_at").first() if ids else None
    if known is not None:
        return known.conversation
    subject = normalize_subject(parsed.subject)
    contact = parsed.reply_to or parsed.from_email
    candidates = Conversation.objects.filter(contact_email__iexact=contact, last_message_at__gte=parsed.date - timedelta(days=60))
    return next((c for c in candidates if normalize_subject(c.subject) == subject), None)


def match(conversation: Conversation, text: str, sender: str = "") -> None:
    """Put the conversation with a customer account, if one can be told: the login or billing
    address it came from, else an order or ticket number of ours in the text. A number alone is
    a hint, not proof of who wrote: the page shows the sender's address next to the account."""
    if conversation.account_id:
        return
    account = account_for_address(sender or conversation.contact_email)
    if account is None:
        from apps.billing.models import Order
        from apps.support.models import Ticket

        upper = text.upper()
        order = Order.objects.filter(ref__in=ORDER_REF.findall(upper)).select_related("account").first()
        ticket = None if order else Ticket.objects.filter(public_id__in=TICKET_ID.findall(upper)).select_related("account").first()
        account = order.account if order else ticket.account if ticket else None
    conversation.account = account


@transaction.atomic
def store(raw: bytes, *, uid: int | None = None, uidvalidity: int | None = None) -> bool:
    """Add one received mail; ``False`` if it is already known."""
    parsed = parse(raw)
    if InboxMessage.objects.filter(message_id=parsed.message_id).exists():
        return False
    conversation = find_conversation(parsed)
    if conversation is None:
        conversation = Conversation(
            subject=parsed.subject,
            contact_email=parsed.reply_to or parsed.from_email or "unknown@invalid",
            contact_name=parsed.from_name,
            is_automatic=parsed.automatic,
            status=Conversation.Status.DONE if parsed.automatic else Conversation.Status.NEW,
            is_read=parsed.automatic,
            last_message_at=parsed.date,
        )
    elif not parsed.automatic:
        conversation.status, conversation.is_automatic, conversation.is_read = Conversation.Status.NEW, False, False
    conversation.last_message_at = max(conversation.last_message_at, parsed.date)
    match(conversation, f"{parsed.subject}\n{parsed.body[:20000]}", sender=parsed.from_email)
    conversation.save()
    InboxMessage.objects.create(
        conversation=conversation,
        direction=InboxMessage.Direction.IN,
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        references=parsed.references,
        uidvalidity=uidvalidity,
        uid=uid,
        from_email=parsed.from_email,
        from_name=parsed.from_name,
        reply_to=parsed.reply_to,
        to=parsed.to,
        cc=parsed.cc,
        subject=parsed.subject,
        body=parsed.body,
        attachments=parsed.attachments,
        sent_at=parsed.date,
    )
    return True


def fetch(config: MailboxSettings | None = None) -> int:
    """Read new mails from the server. Returns how many were added; errors land in the settings row."""
    config = config or MailboxSettings.load()
    added = 0
    try:
        with open_mailbox(config) as box:
            validity = box.select("INBOX", readonly=True)
            if validity != config.uidvalidity:  # first run, or the server renumbered
                config.uidvalidity, config.last_uid = validity, 0
                uids = box.uids_since(timezone.localdate() - timedelta(days=config.initial_days))
            else:
                uids = box.uids_after(config.last_uid)
            for uid in uids[:MAX_PER_RUN]:
                raw = box.fetch(uid)
                if raw and store(raw, uid=uid, uidvalidity=validity):
                    added += 1
                config.last_uid = max(config.last_uid, uid)
        config.last_error = ""
    except MailboxError as exc:
        config.last_error = str(exc)[:300]
    except (OSError, imaplib.IMAP4.error) as exc:  # the connection broke while reading
        config.last_error = f"{exc.__class__.__name__}: {exc}"[:300]
    config.last_fetch_at = timezone.now()
    config.save(update_fields=["uidvalidity", "last_uid", "last_error", "last_fetch_at"])
    return added


# --------------------------------------------------------------------------- replying


def sender_address() -> str:
    """Replies come from the mailbox customers wrote to, so their answer lands there again."""
    user = MailboxSettings.load().effective_user
    return user if "@" in user else parseaddr(settings.DEFAULT_FROM_EMAIL)[1]


def _quote(message: InboxMessage | None) -> str:
    if message is None or not message.body:
        return ""
    when = timezone.localtime(message.sent_at).strftime("%d.%m.%Y %H:%M")
    sender = formataddr((message.from_name, message.from_email)) if message.from_name else message.from_email
    return f"\n\nOn {when}, {sender} wrote:\n" + "\n".join(f"> {line}" for line in message.body[:4000].splitlines())


def last_received(conversation: Conversation) -> InboxMessage | None:
    return conversation.messages.filter(direction=InboxMessage.Direction.IN).order_by("-sent_at", "-pk").first()


def send_reply(conversation: Conversation, body: str, *, user) -> InboxMessage:
    """Send the reply (raises if the mail server refuses) and record it in the conversation."""
    original = last_received(conversation)
    to = (original.reply_to or original.from_email) if original else conversation.contact_email
    subject = conversation.subject or (original.subject if original else "")
    if original is not None and not re.match(r"^\s*(re|aw)\s*:", subject, re.I):
        subject = f"Re: {subject}".strip()
    sender = sender_address()
    message_id = make_msgid(domain=sender.rsplit("@", 1)[-1] if "@" in sender else None)
    references = " ".join(filter(None, [original.references, original.message_id])) if original else ""
    headers = {"Message-ID": message_id, SKIP_HEADER: "skip"}  # recorded below, with who sent it
    if original:
        headers.update({"In-Reply-To": original.message_id, "References": references[-5000:]})
    mail = EmailMessage(subject, body.strip() + "\n" + _quote(original), formataddr((settings.COMPANY["NAME"], sender)), [to], headers=headers)
    try:
        mail.send()
    except Exception as exc:
        MailLog.objects.create(
            to=to, subject=subject[:300], body=body.strip(), status=MailLog.Status.FAILED,
            error=f"{exc.__class__.__name__}: {exc}"[:500], account=conversation.account,
        )
        raise
    record = InboxMessage.objects.create(
        conversation=conversation,
        direction=InboxMessage.Direction.OUT,
        message_id=message_id,
        in_reply_to=original.message_id if original else "",
        references=references[-5000:],
        from_email=sender,
        from_name=settings.COMPANY["NAME"],
        to=to,
        subject=subject[:300],
        body=body.strip(),
        sent_by=user,
    )
    conversation.status, conversation.is_read, conversation.last_message_at = Conversation.Status.ANSWERED, True, record.sent_at
    conversation.save(update_fields=["status", "is_read", "last_message_at"])
    return record
