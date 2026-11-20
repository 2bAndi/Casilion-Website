"""Products, plans, subscriptions, orders and payments.

A *product* is something Zuri-AI sells licenses for (Cassilion, Zuri-AI, Vakaluja). A *plan* is
one yearly license of a product, from free to paid. An *order* has lines; each line buys one
plan for one term. A *payment* is one attempt to pay an order by one method.
"""
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedTextField

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: customers type this into a bank transfer


class Product(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    tagline = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    license_prefix = models.CharField(max_length=4, default="CAS", help_text="First letters of the license IDs, e.g. CAS.")
    is_public = models.BooleanField(default=True, help_text="Shown on the plans page.")
    rank = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["rank", "name"]

    def __str__(self):
        return self.name

    @classmethod
    def default(cls) -> "Product":
        return cls.objects.get(code=settings.CASSILION["DEFAULT_PRODUCT"])

    def free_plan(self) -> "Plan | None":
        return self.plans.filter(price_eur_year=0, self_service=True).order_by("rank").first()


class Plan(models.Model):
    """A yearly license of one product. The free one carries no support; the others are support plans."""

    class SupportLevel(models.TextChoices):
        NONE = "none", "No support"
        AI = "ai", "Support AI"
        AI_HUMAN = "ai_human", "Support AI, a person on request"
        PRIORITY = "priority", "Priority, a person directly"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="plans")
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=60)
    tagline = models.CharField(max_length=160, blank=True)
    price_eur_year = models.PositiveIntegerField(default=0, help_text="Final price per year in euros, VAT included.")
    support_level = models.CharField(max_length=12, choices=SupportLevel.choices, default=SupportLevel.NONE)
    ticket_quota_month = models.PositiveIntegerField(default=0, help_text="Support tickets per calendar month.")
    bug_report_quota_month = models.PositiveIntegerField(default=2, help_text="One-way bug reports per calendar month.")
    self_service = models.BooleanField(default=True, help_text="Can be bought in the shop. Off = by quote.")
    is_public = models.BooleanField(default=True)
    rank = models.PositiveSmallIntegerField(default=0, help_text="Higher rank = larger plan.")

    class Meta:
        ordering = ["product__rank", "rank"]

    def __str__(self):
        return f"{self.product.name} {self.name}"

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
    return "CO-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(7))


class Subscription(models.Model):
    """One paid plan of one account, term by term. Each term is paid by its own order."""

    class Status(models.TextChoices):
        INCOMPLETE = "incomplete", "Waiting for payment"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Payment overdue"
        CANCELLED = "cancelled", "Cancelled"
        ENDED = "ended", "Ended"

    LIVE = (Status.ACTIVE, Status.PAST_DUE, Status.CANCELLED)

    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    provider = models.CharField(max_length=30, blank=True)
    provider_ref = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.INCOMPLETE)
    period_end = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account} – {self.plan} ({self.status})"


class PaymentMethod(models.TextChoices):
    STRIPE = "stripe", "Card, Apple Pay, Google Pay"
    PAYPAL = "paypal", "PayPal"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    SANDBOX = "sandbox", "Sandbox (no money moves)"


class Order(models.Model):
    class Kind(models.TextChoices):
        NEW = "new", "New plan"
        RENEWAL = "renewal", "Renewal"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
        REFUNDED = "refunded", "Refunded"

    SETTLED = (Status.PAID, Status.PARTIALLY_REFUNDED, Status.REFUNDED)

    ref = models.CharField(max_length=40, unique=True, default=_order_ref)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.NEW)
    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="orders")
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    total = models.DecimalField(max_digits=10, decimal_places=2, help_text="Final amount in euros, VAT included.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, blank=True)
    language = models.CharField(max_length=8, default="en")
    billing = models.JSONField(default=dict, blank=True, help_text="Invoice recipient as given when the order was placed.")
    due_date = models.DateField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)
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
        return f"{self.ref} {self.status}"

    @property
    def first_line(self):
        return next(iter(self.lines.all()), None)

    @property
    def plan(self):
        line = self.first_line
        return line.plan if line else None

    @property
    def subscription(self):
        line = self.first_line
        return line.subscription if line else None

    @property
    def refunded_total(self) -> Decimal:
        return sum((refund.amount for refund in self.refunds.all()), Decimal("0"))

    @property
    def email(self) -> str:
        """Where documents about this order go."""
        tax = getattr(self.account, "tax_profile", None)
        if tax and tax.billing_email:
            return tax.billing_email
        return self.placed_by.email if self.placed_by else ""


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="+")
    subscription = models.ForeignKey(Subscription, null=True, blank=True, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="VAT included.")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("19"))
    total = models.DecimalField(max_digits=10, decimal_places=2)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.ref}: {self.description}"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"  # accepted, money not there yet
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"
    DISPUTED = "disputed", "Disputed"  # chargeback or dispute open
    CHARGED_BACK = "charged_back", "Charged back"  # dispute lost


# The money arrived (and may since have gone back in part): refunds and reconciliation use these.
COLLECTED = (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)
OPEN = (PaymentStatus.PENDING, PaymentStatus.PROCESSING)


class Payment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=20, choices=PaymentMethod.choices)
    provider_id = models.CharField(max_length=100, blank=True)
    provider_ref = models.CharField(
        max_length=100, blank=True, db_index=True, help_text="The provider's first id for it (Stripe checkout session, PayPal order)."
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True)
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_id"], condition=~models.Q(provider_id=""), name="payment_provider_id_unique")
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_id} {self.amount}"


class Refund(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="refunds")
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=300, blank=True)
    provider_id = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.ref} -{self.amount}"


class PaymentEvent(models.Model):
    """Append-only history of payments.

    One row per provider message (also ignored and rejected ones, each provider event once),
    per status change and per action of the team, with its cause. Never updated or deleted.
    """

    class Source(models.TextChoices):
        WEBHOOK = "webhook", "Payment provider"
        STAFF = "staff", "Team"
        SYSTEM = "system", "System"
        JOB = "job", "Background job"
        IMPORT = "import", "Bank statement"
        CUSTOMER = "customer", "Customer"

    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name="payment_events")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.WEBHOOK)
    kind = models.CharField(max_length=30, blank=True)
    provider = models.CharField(max_length=30, blank=True)
    provider_event_id = models.CharField(max_length=120, blank=True)
    provider_type = models.CharField(max_length=80, blank=True, help_text="Event type as the provider names it.")
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status_from = models.CharField(max_length=20, blank=True)
    status_to = models.CharField(max_length=20, blank=True)
    outcome = models.CharField(max_length=120, blank=True)
    note = models.CharField(max_length=300, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    raw = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True, help_text="Time at the provider.")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_event_id"],
                condition=models.Q(source="webhook") & ~models.Q(provider_event_id=""),
                name="one_row_per_provider_event",
            )
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_event_id} {self.kind}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Payment events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Payment events are append-only.")


class ReconciliationIssue(models.Model):
    """Something about money that a person has to look at."""

    class Kind(models.TextChoices):
        MISSING_LOCAL = "missing_local", "Provider has it, we do not"
        MISSING_REMOTE = "missing_remote", "We have it, provider does not"
        AMOUNT = "amount", "Amount differs"
        DISPUTE = "dispute", "Chargeback / dispute"
        EXTERNAL_REFUND = "external_refund", "Refunded at the provider"
        UNKNOWN = "unknown", "Message for an unknown payment"
        LATE_PAYMENT = "late_payment", "Money for a cancelled order"
        UNPAID_INVOICE = "unpaid_invoice", "Renewal invoice not paid"
        INVOICE_MISSING = "invoice_missing", "Invoice could not be created"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    provider = models.CharField(max_length=20, blank=True)
    provider_id = models.CharField(max_length=100, blank=True)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="issues")
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_id", "kind"], condition=models.Q(resolved=False), name="one_open_issue_per_kind")
        ]

    def __str__(self):
        return f"{self.kind} {self.provider_id}"


class PaymentSettings(models.Model):
    """Payment accounts and what invoices need beyond the company data in the settings. One row.

    Secrets are stored encrypted and are never shown again after they were entered.
    """

    methods = models.CharField(
        max_length=100, default="stripe,paypal,bank_transfer", help_text="Methods offered to customers, once they are configured."
    )
    stripe_secret_key = EncryptedTextField(blank=True)
    stripe_webhook_secret = EncryptedTextField(blank=True)
    paypal_client_id = models.CharField(max_length=200, blank=True)
    paypal_secret = EncryptedTextField(blank=True)
    paypal_webhook_id = models.CharField(max_length=100, blank=True)
    paypal_sandbox = models.BooleanField(default=True, help_text="Use PayPal's sandbox (test) instead of the live system.")
    bank_account_holder = models.CharField(max_length=200, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True)
    bic = models.CharField("BIC", max_length=20, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    # Invoices
    invoice_prefix = models.CharField(max_length=10, default="RE")
    tax_number = models.CharField("Tax number (Steuernummer)", max_length=40, blank=True)
    vat_rate = models.DecimalField("VAT rate in percent", max_digits=5, decimal_places=2, default=Decimal("19"))
    small_business = models.BooleanField("Small business (§ 19 UStG)", default=False, help_text="No VAT is shown on invoices.")
    finance_email = models.EmailField(blank=True, help_text="Gets the payment alerts. Empty = the operators.")
    # Hand-over to the tax adviser (DATEV)
    datev_consultant_number = models.CharField(max_length=10, blank=True)
    datev_client_number = models.CharField(max_length=10, blank=True)
    datev_revenue_account = models.CharField(max_length=10, default="8400")
    datev_bank_account = models.CharField(max_length=10, default="1200")
    datev_clearing_account = models.CharField(max_length=10, default="1360", help_text="Money in transit at Stripe or PayPal.")

    class Meta:
        verbose_name = verbose_name_plural = "Payment settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "PaymentSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Payment settings"

    @property
    def method_list(self) -> list[str]:
        return [m.strip() for m in self.methods.split(",") if m.strip()]
