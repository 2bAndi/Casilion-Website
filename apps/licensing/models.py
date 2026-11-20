import uuid

from django.db import models


class Entitlement(models.Model):
    """Account X has plan Y from one date to another. The single source of truth for what an account may do."""

    class Source(models.TextChoices):
        FREE = "free", "Free registration"
        SUBSCRIPTION = "subscription", "Paid subscription"
        MANUAL = "manual", "Granted by the team"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="entitlements")
    plan = models.ForeignKey("billing.Plan", on_delete=models.PROTECT, related_name="entitlements")
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True, help_text="Empty = no end date.")
    source = models.CharField(max_length=14, choices=Source.choices, default=Source.FREE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account}: {self.plan}"


class License(models.Model):
    """An entitlement in a form the database can verify offline: a signed JSON document."""

    license_id = models.CharField(max_length=24, unique=True)
    entitlement = models.ForeignKey(Entitlement, on_delete=models.CASCADE, related_name="licenses")
    key_id = models.CharField(max_length=60)
    payload = models.JSONField()
    signature = models.TextField()
    issued_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return self.license_id
