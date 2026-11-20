"""Invoices and credit notes: gapless numbering, never changed, never deleted."""
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone
from django.utils.deconstruct import deconstructible


@deconstructible
class InvoiceStorage(FileSystemStorage):
    """Files outside the web root, in ``CASSILION["INVOICE_DIR"]``. Served only through views that check who asks."""

    @property
    def base_location(self):
        return str(settings.CASSILION["INVOICE_DIR"])

    @property
    def location(self):
        return os.path.abspath(self.base_location)


class InvoiceKind(models.TextChoices):
    INVOICE = "invoice", "Invoice"
    CREDIT_NOTE = "credit_note", "Credit note"


class InvoicePurpose(models.TextChoices):
    """Why a document exists beyond the normal sale and refunds."""

    NORMAL = "", "Normal"
    CANCELLATION = "cancellation", "Cancellation"  # credit note that voids an invoice entirely
    CORRECTION = "correction", "Correction"  # invoice that replaces a voided one


class ImmutableError(Exception):
    pass


class InvoiceSequence(models.Model):
    """One counter per year; invoices and credit notes share it."""

    year = models.PositiveSmallIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.year}: {self.last_number}"


class Invoice(models.Model):
    number = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=12, choices=InvoiceKind.choices, default=InvoiceKind.INVOICE)
    order = models.ForeignKey("billing.Order", on_delete=models.PROTECT, related_name="invoices")
    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="invoices")
    refers_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="credit_notes")
    purpose = models.CharField(max_length=12, choices=InvoicePurpose.choices, default=InvoicePurpose.NORMAL, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True, help_text="Set on invoices that were issued before the money arrived.")
    service_from = models.DateField()
    service_to = models.DateField()
    language = models.CharField(max_length=8, default="de")
    seller = models.JSONField(help_text="Letterhead as printed.")
    recipient = models.JSONField(help_text="Recipient lines as printed.")
    lines = models.JSONField()
    net_total = models.DecimalField(max_digits=10, decimal_places=2)
    tax_total = models.DecimalField(max_digits=10, decimal_places=2)
    gross_total = models.DecimalField(max_digits=10, decimal_places=2)
    tax_breakdown = models.JSONField(default=list)
    small_business = models.BooleanField(default=False)
    payment_method = models.CharField(max_length=20, blank=True)
    pdf = models.FileField(upload_to="%Y/", storage=InvoiceStorage())
    pdf_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-number"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableError("Invoices cannot be changed. Issue a credit note instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Invoices cannot be deleted. Issue a credit note instead.")

    @property
    def is_credit_note(self) -> bool:
        return self.kind == InvoiceKind.CREDIT_NOTE

    def read(self) -> bytes:
        with self.pdf.open("rb") as handle:
            return handle.read()
