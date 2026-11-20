import secrets

from django.conf import settings
from django.db import models


class Plan(models.Model):
    """A license plan. Community is free and carries no support; the others are yearly support plans."""

    class SupportLevel(models.TextChoices):
        NONE = "none", "No support"
        AI = "ai", "Support AI"
        AI_HUMAN = "ai_human", "Support AI, a person on request"
        PRIORITY = "priority", "Priority, a person directly"

    code = models.SlugField(unique=True)
    name = models.CharField(max_length=60)
    tagline = models.CharField(max_length=160, blank=True)
    price_eur_year = models.PositiveIntegerField(default=0)
    support_level = models.CharField(max_length=12, choices=SupportLevel.choices, default=SupportLevel.NONE)
    ticket_quota_month = models.PositiveIntegerField(default=0, help_text="Support tickets per calendar month.")
    bug_report_quota_month = models.PositiveIntegerField(default=2, help_text="One-way bug reports per calendar month.")
    self_service = models.BooleanField(default=True, help_text="Can be bought in the shop. Off = by quote.")
    is_public = models.BooleanField(default=True)
    rank = models.PositiveSmallIntegerField(default=0, help_text="Higher rank = larger plan.")

    class Meta:
        ordering = ["rank"]

    def __str__(self):
        return self.name

    @property
    def is_free(self) -> bool:
        return self.price_eur_year == 0 and self.self_service

    @property
    def has_support(self) -> bool:
        return self.support_level != self.SupportLevel.NONE

    @property
    def human_on_request(self) -> bool:
        return self.support_level in {self.SupportLevel.AI_HUMAN, self.SupportLevel.PRIORITY}


def _order_ref() -> str:
    return "ord_" + secrets.token_urlsafe(12)


class Subscription(models.Model):
    class Status(models.TextChoices):
        INCOMPLETE = "incomplete", "Waiting for payment"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Payment overdue"
        CANCELLED = "cancelled", "Cancelled"
        ENDED = "ended", "Ended"

    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    provider = models.CharField(max_length=30)
    provider_ref = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.INCOMPLETE)
    period_end = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account} – {self.plan} ({self.status})"


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    ref = models.CharField(max_length=40, unique=True, default=_order_ref)
    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="orders")
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="orders")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    amount_eur = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # What the customer declared on the order page. Kept as evidence, never changed afterwards.
    early_start_requested_at = models.DateTimeField(
        null=True, blank=True, help_text="A consumer asked for support to start before the withdrawal period ends."
    )
    terms_version = models.CharField(max_length=20, blank=True, help_text="Date of the terms the order was placed under.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ref} {self.plan} {self.status}"


class PaymentEvent(models.Model):
    """Every provider webhook, stored once. Makes webhook handling idempotent and auditable."""

    provider = models.CharField(max_length=30)
    event_id = models.CharField(max_length=120)
    type = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="unique_provider_event")]
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.provider}:{self.event_id} {self.type}"
