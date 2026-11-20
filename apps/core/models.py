import secrets

from django.conf import settings
from django.db import models


def _token() -> str:
    return secrets.token_urlsafe(24)


def _reference() -> str:
    return "ntc_" + secrets.token_hex(5)


class ContractNotice(models.Model):
    """A withdrawal or a cancellation declared through the website.

    German law asks for both functions when contracts are sold online to consumers
    (§ 356a BGB: withdrawal function, § 312k BGB: cancellation button). A row here is the
    declaration as it arrived, with the time of receipt. It is never edited afterwards;
    what the team did about it goes into the handling fields.
    """

    class Kind(models.TextChoices):
        WITHDRAWAL = "withdrawal", "Withdrawal"
        CANCELLATION = "cancellation", "Cancellation"

    class CancelType(models.TextChoices):
        ORDINARY = "ordinary", "Ordinary cancellation"
        EXTRAORDINARY = "extraordinary", "Cancellation for cause"

    reference = models.CharField(max_length=20, unique=True, default=_reference, editable=False)
    token = models.CharField(max_length=48, unique=True, default=_token, editable=False)
    kind = models.CharField(max_length=14, choices=Kind.choices)
    name = models.CharField(max_length=200)
    email = models.EmailField()
    contract = models.CharField(max_length=300, help_text="How the customer identified the contract: order number, plan, date.")
    cancel_type = models.CharField(max_length=14, choices=CancelType.choices, blank=True)
    cancel_reason = models.TextField(blank=True, help_text="Only for a cancellation for cause.")
    cancel_at = models.CharField(max_length=60, blank=True, help_text="When the contract should end, in the customer's words.")
    language = models.CharField(max_length=2, default="en")
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmation_sent = models.BooleanField(default=False)
    handled_at = models.DateTimeField(null=True, blank=True)
    handling_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.reference} {self.kind} {self.email}"
