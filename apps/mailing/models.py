"""Mail with customers: a log of what the site sent, and the mailbox customers write to.

Taken over from the Tangosummer ticket shop and cut down: the mail server stays the archive,
this is the working copy the backoffice shows next to the customer.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedTextField


class MailLog(models.Model):
    """One mail the site sent (or tried to send). Sign-in links are not stored."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    to = models.CharField(max_length=500)
    subject = models.CharField(max_length=300)
    body = models.TextField(blank=True, help_text="Text of the mail as sent, without sign-in links.")
    attachments = models.JSONField(default=list, blank=True, help_text="File names.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT)
    error = models.CharField(max_length=500, blank=True)
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="mail_logs")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.to}: {self.subject}"


class MailboxSettings(models.Model):
    """The mailbox customers write to (e.g. info@), read over IMAP. One row.

    Empty server, user and password mean: the account the site already sends with
    (EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD in the server configuration).
    """

    enabled = models.BooleanField("Read the mailbox", default=False, help_text="Fetch new mails every few minutes. Nothing is changed or deleted on the mail server.")
    host = models.CharField("IMAP server", max_length=200, blank=True, help_text="Empty = the server the site sends with.")
    port = models.PositiveIntegerField("IMAP port (TLS)", default=993)
    user = models.CharField("User name", max_length=200, blank=True, help_text="Empty = the account the site sends with.")
    password = EncryptedTextField(blank=True)
    initial_days = models.PositiveSmallIntegerField("Days to read back on the first run", default=30)
    # Where the last fetch stopped (IMAP UIDs are per UIDVALIDITY).
    uidvalidity = models.BigIntegerField(default=0)
    last_uid = models.BigIntegerField(default=0)
    last_fetch_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "Mailbox settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "MailboxSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Mailbox settings"

    @property
    def effective_host(self) -> str:
        return self.host or getattr(settings, "EMAIL_HOST", "") or ""

    @property
    def effective_user(self) -> str:
        return self.user or getattr(settings, "EMAIL_HOST_USER", "") or ""

    @property
    def effective_password(self) -> str:
        return self.password or (getattr(settings, "EMAIL_HOST_PASSWORD", "") if not self.user else "")


class Conversation(models.Model):
    """One e-mail thread with somebody: their mails and our replies."""

    class Status(models.TextChoices):
        NEW = "new", "To answer"
        ANSWERED = "answered", "Answered"
        DONE = "done", "Done"

    subject = models.CharField(max_length=300, blank=True)
    contact_email = models.EmailField(max_length=254, db_index=True)
    contact_name = models.CharField(max_length=200, blank=True)
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="conversations")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    is_read = models.BooleanField(default=False)
    is_automatic = models.BooleanField(default=False, help_text="Auto-reply, newsletter or system mail: no answer expected.")
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return f"{self.contact_email}: {self.subject}"


class InboxMessage(models.Model):
    """One mail of a conversation. Text only; attachments stay on the mail server."""

    class Direction(models.TextChoices):
        IN = "in", "Received"
        OUT = "out", "Sent"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    message_id = models.CharField(max_length=300, db_index=True)
    in_reply_to = models.CharField(max_length=300, blank=True)
    references = models.TextField(blank=True)
    uidvalidity = models.BigIntegerField(null=True, blank=True)
    uid = models.BigIntegerField(null=True, blank=True)
    from_email = models.CharField(max_length=254)
    from_name = models.CharField(max_length=200, blank=True)
    reply_to = models.CharField(max_length=254, blank=True)
    to = models.CharField(max_length=500, blank=True)
    cc = models.CharField(max_length=500, blank=True)
    subject = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True, help_text="Plain text; HTML mails are converted.")
    attachments = models.JSONField(default=list, blank=True, help_text="Name, type, size per file.")
    sent_at = models.DateTimeField(default=timezone.now)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["sent_at", "pk"]

    def __str__(self):
        return f"{self.direction} {self.subject}"
