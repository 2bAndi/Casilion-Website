"""Products with their own plans, orders with lines, payments with a history.

Written by hand because existing rows are carried over, in three steps: this one adds the new
tables and columns, 0005 moves the data, 0006 removes what is no longer needed. They are
separate migrations because PostgreSQL does not change a table in the transaction that has
just written to it.
"""
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models

import apps.billing.models
import apps.core.fields

def _fk(to, **kwargs):
    return models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=to, **kwargs)


def _user(**kwargs):
    return models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, **kwargs)


_ID = ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"))
_METHODS = [("stripe", "Card, Apple Pay, Google Pay"), ("paypal", "PayPal"), ("bank_transfer", "Bank transfer"), ("sandbox", "Sandbox (no money moves)")]
_PAYMENT_STATES = [
    ("pending", "Pending"), ("processing", "Processing"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled"),
    ("partially_refunded", "Partially refunded"), ("refunded", "Refunded"), ("disputed", "Disputed"), ("charged_back", "Charged back"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_legal_functions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- products -------------------------------------------------------------------
        migrations.CreateModel(
            name="Product",
            fields=[
                _ID,
                ("code", models.SlugField(unique=True)),
                ("name", models.CharField(max_length=80)),
                ("tagline", models.CharField(blank=True, max_length=200)),
                ("description", models.TextField(blank=True)),
                ("license_prefix", models.CharField(default="CAS", help_text="First letters of the license IDs, e.g. CAS.", max_length=4)),
                ("is_public", models.BooleanField(default=True, help_text="Shown on the plans page.")),
                ("rank", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"ordering": ["rank", "name"]},
        ),
        migrations.AddField(model_name="plan", name="product", field=_fk("billing.product", related_name="plans", null=True)),
        migrations.AlterModelOptions(name="plan", options={"ordering": ["product__rank", "rank"]}),
        migrations.AlterField(
            model_name="plan",
            name="price_eur_year",
            field=models.PositiveIntegerField(default=0, help_text="Final price per year in euros, VAT included."),
        ),
        migrations.AlterField(model_name="subscription", name="provider", field=models.CharField(blank=True, max_length=30)),
        # --- orders ---------------------------------------------------------------------
        migrations.RenameField(model_name="order", old_name="amount_eur", new_name="total"),
        migrations.AlterField(
            model_name="order",
            name="total",
            field=models.DecimalField(decimal_places=2, help_text="Final amount in euros, VAT included.", max_digits=10),
        ),
        migrations.RenameField(model_name="order", old_name="plan", new_name="old_plan"),
        migrations.RenameField(model_name="order", old_name="subscription", new_name="old_subscription"),
        migrations.AlterField(model_name="order", name="ref", field=models.CharField(default=apps.billing.models._order_ref, max_length=40, unique=True)),
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"), ("paid", "Paid"), ("failed", "Failed"), ("cancelled", "Cancelled"),
                    ("partially_refunded", "Partially refunded"), ("refunded", "Refunded"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="order", name="kind", field=models.CharField(choices=[("new", "New plan"), ("renewal", "Renewal")], default="new", max_length=10)
        ),
        migrations.AddField(model_name="order", name="payment_method", field=models.CharField(blank=True, choices=_METHODS, max_length=20)),
        migrations.AddField(model_name="order", name="language", field=models.CharField(default="en", max_length=8)),
        migrations.AddField(
            model_name="order",
            name="billing",
            field=models.JSONField(blank=True, default=dict, help_text="Invoice recipient as given when the order was placed."),
        ),
        migrations.AddField(model_name="order", name="due_date", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="order", name="reminded_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.CreateModel(
            name="OrderLine",
            fields=[
                _ID,
                ("description", models.CharField(max_length=200)),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("unit_price", models.DecimalField(decimal_places=2, help_text="VAT included.", max_digits=10)),
                ("tax_rate", models.DecimalField(decimal_places=2, default=Decimal("19"), max_digits=5)),
                ("total", models.DecimalField(decimal_places=2, max_digits=10)),
                ("period_from", models.DateField(blank=True, null=True)),
                ("period_to", models.DateField(blank=True, null=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="billing.order")),
                ("plan", _fk("billing.plan", related_name="+")),
                ("subscription", _fk("billing.subscription", related_name="lines", blank=True, null=True)),
            ],
            options={"ordering": ["id"]},
        ),
        # --- payments -------------------------------------------------------------------
        migrations.CreateModel(
            name="Payment",
            fields=[
                _ID,
                ("provider", models.CharField(choices=_METHODS, max_length=20)),
                ("provider_id", models.CharField(blank=True, max_length=100)),
                (
                    "provider_ref",
                    models.CharField(
                        blank=True, db_index=True, help_text="The provider's first id for it (Stripe checkout session, PayPal order).", max_length=100
                    ),
                ),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("status", models.CharField(choices=_PAYMENT_STATES, db_index=True, default="pending", max_length=20)),
                ("raw", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("order", _fk("billing.order", related_name="payments")),
            ],
            options={
                "ordering": ["id"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("provider_id", ""), _negated=True), fields=("provider", "provider_id"), name="payment_provider_id_unique"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Refund",
            fields=[
                _ID,
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("reason", models.CharField(blank=True, max_length=300)),
                ("provider_id", models.CharField(blank=True, max_length=100)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_by", _user()),
                ("order", _fk("billing.order", related_name="refunds")),
                ("payment", _fk("billing.payment", related_name="refunds")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="ReconciliationIssue",
            fields=[
                _ID,
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("missing_local", "Provider has it, we do not"), ("missing_remote", "We have it, provider does not"),
                            ("amount", "Amount differs"), ("dispute", "Chargeback / dispute"), ("external_refund", "Refunded at the provider"),
                            ("unknown", "Message for an unknown payment"), ("late_payment", "Money for a cancelled order"),
                            ("unpaid_invoice", "Renewal invoice not paid"), ("invoice_missing", "Invoice could not be created"),
                        ],
                        max_length=20,
                    ),
                ),
                ("provider", models.CharField(blank=True, max_length=20)),
                ("provider_id", models.CharField(blank=True, max_length=100)),
                ("detail", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("resolved", models.BooleanField(default=False)),
                ("order", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="issues", to="billing.order")),
                ("resolved_by", _user()),
            ],
            options={
                "ordering": ["-created_at"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("resolved", False)), fields=("provider", "provider_id", "kind"), name="one_open_issue_per_kind"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="PaymentSettings",
            fields=[
                _ID,
                (
                    "methods",
                    models.CharField(default="stripe,paypal,bank_transfer", help_text="Methods offered to customers, once they are configured.", max_length=100),
                ),
                ("stripe_secret_key", apps.core.fields.EncryptedTextField(blank=True)),
                ("stripe_webhook_secret", apps.core.fields.EncryptedTextField(blank=True)),
                ("paypal_client_id", models.CharField(blank=True, max_length=200)),
                ("paypal_secret", apps.core.fields.EncryptedTextField(blank=True)),
                ("paypal_webhook_id", models.CharField(blank=True, max_length=100)),
                ("paypal_sandbox", models.BooleanField(default=True, help_text="Use PayPal's sandbox (test) instead of the live system.")),
                ("bank_account_holder", models.CharField(blank=True, max_length=200)),
                ("iban", models.CharField(blank=True, max_length=40, verbose_name="IBAN")),
                ("bic", models.CharField(blank=True, max_length=20, verbose_name="BIC")),
                ("bank_name", models.CharField(blank=True, max_length=100)),
                ("invoice_prefix", models.CharField(default="RE", max_length=10)),
                ("tax_number", models.CharField(blank=True, max_length=40, verbose_name="Tax number (Steuernummer)")),
                ("vat_rate", models.DecimalField(decimal_places=2, default=Decimal("19"), max_digits=5, verbose_name="VAT rate in percent")),
                (
                    "small_business",
                    models.BooleanField(default=False, help_text="No VAT is shown on invoices.", verbose_name="Small business (§ 19 UStG)"),
                ),
                ("finance_email", models.EmailField(blank=True, help_text="Gets the payment alerts. Empty = the operators.", max_length=254)),
                ("datev_consultant_number", models.CharField(blank=True, max_length=10)),
                ("datev_client_number", models.CharField(blank=True, max_length=10)),
                ("datev_revenue_account", models.CharField(default="8400", max_length=10)),
                ("datev_bank_account", models.CharField(default="1200", max_length=10)),
                ("datev_clearing_account", models.CharField(default="1360", help_text="Money in transit at Stripe or PayPal.", max_length=10)),
            ],
            options={"verbose_name": "Payment settings", "verbose_name_plural": "Payment settings"},
        ),
        # --- the webhook log becomes the payment history ----------------------------------
        migrations.RemoveConstraint(model_name="paymentevent", name="unique_provider_event"),
        migrations.RenameField(model_name="paymentevent", old_name="event_id", new_name="provider_event_id"),
        migrations.RenameField(model_name="paymentevent", old_name="type", new_name="provider_type"),
        migrations.RenameField(model_name="paymentevent", old_name="payload", new_name="raw"),
        migrations.RenameField(model_name="paymentevent", old_name="received_at", new_name="created_at"),
        migrations.RenameField(model_name="paymentevent", old_name="error", new_name="old_error"),
        migrations.RemoveField(model_name="paymentevent", name="processed_at"),
        migrations.AlterField(model_name="paymentevent", name="provider", field=models.CharField(blank=True, max_length=30)),
        migrations.AlterField(model_name="paymentevent", name="provider_event_id", field=models.CharField(blank=True, max_length=120)),
        migrations.AlterField(
            model_name="paymentevent",
            name="provider_type",
            field=models.CharField(blank=True, help_text="Event type as the provider names it.", max_length=80),
        ),
        migrations.AlterField(model_name="paymentevent", name="raw", field=models.JSONField(blank=True, default=dict)),
        migrations.AlterField(model_name="paymentevent", name="created_at", field=models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
        migrations.AddField(
            model_name="paymentevent",
            name="source",
            field=models.CharField(
                choices=[
                    ("webhook", "Payment provider"), ("staff", "Team"), ("system", "System"), ("job", "Background job"),
                    ("import", "Bank statement"), ("customer", "Customer"),
                ],
                default="webhook",
                max_length=10,
            ),
        ),
        migrations.AddField(model_name="paymentevent", name="kind", field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(model_name="paymentevent", name="amount", field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AddField(model_name="paymentevent", name="status_from", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="paymentevent", name="status_to", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="paymentevent", name="outcome", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="paymentevent", name="note", field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name="paymentevent", name="occurred_at", field=models.DateTimeField(blank=True, help_text="Time at the provider.", null=True)),
        migrations.AddField(model_name="paymentevent", name="actor", field=_user()),
        migrations.AddField(
            model_name="paymentevent",
            name="order",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payment_events", to="billing.order"),
        ),
        migrations.AddField(
            model_name="paymentevent",
            name="payment",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="events", to="billing.payment"),
        ),
        migrations.AlterModelOptions(name="paymentevent", options={"ordering": ["created_at", "pk"]}),
        migrations.AddConstraint(
            model_name="paymentevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source", "webhook"), models.Q(("provider_event_id", ""), _negated=True)),
                fields=("provider", "provider_event_id"),
                name="one_row_per_provider_event",
            ),
        ),
    ]
