"""The money side of the backoffice: products and plans, orders, invoices, payment matters,
bank statements and the payment accounts."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.log import audit
from apps.billing import mails, payments
from apps.billing.events import label as event_label
from apps.billing.models import Order, PaymentSettings, Plan, Product, ReconciliationIssue, Subscription
from apps.billing.providers import ProviderError, is_configured, paypal, stripe
from apps.core.pdf import PdfUnavailable, pdf_view
from apps.invoicing import export
from apps.invoicing import services as invoicing
from apps.invoicing.models import Invoice

from .views import staff_required

# --------------------------------------------------------------------------- products and plans


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "code", "tagline", "description", "license_prefix", "is_public", "rank"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class PlanForm(forms.ModelForm):
    class Meta:
        model = Plan
        fields = [
            "product", "name", "code", "tagline", "price_eur_year", "support_level", "ticket_quota_month",
            "bug_report_quota_month", "self_service", "is_public", "rank",
        ]


@staff_required
def products(request):
    rows = Product.objects.prefetch_related("plans").annotate(
        active=Count("plans__subscriptions", filter=Q(plans__subscriptions__status=Subscription.Status.ACTIVE))
    )
    return render(request, "backoffice/products.html", {"products": rows})


def _edit(request, form, *, title: str, what: str, hint: str = ""):
    if request.method == "POST" and form.is_valid():
        changed = sorted(form.changed_data)
        obj = form.save()
        # Who changed what; the values themselves are in the database, not in the audit line.
        audit(f"{what}.saved", actor=request.user, actor_kind="operator", obj=getattr(obj, "code", str(obj.pk)), data={"fields": changed})
        messages.success(request, f"{obj} saved.")
        return redirect("backoffice:products")
    return render(request, "backoffice/form.html", {"form": form, "title": title, "hint": hint, "back": reverse("backoffice:products")})


@staff_required
def product_edit(request, code=None):
    product = get_object_or_404(Product, code=code) if code else None
    form = ProductForm(request.POST or None, instance=product)
    hint = "The code appears in license files and addresses; change it only before the first license is issued."
    return _edit(request, form, title=f"Product {product.name}" if product else "New product", what="product", hint=hint)


@staff_required
def plan_edit(request, code=None):
    plan = get_object_or_404(Plan, code=code) if code else None
    initial = {"product": request.GET.get("product")} if not plan else None
    form = PlanForm(request.POST or None, instance=plan, initial=initial)
    hint = "The price is the final price per year in euros, VAT included. A new price applies to new orders and to the next renewal invoice; running terms keep theirs."
    return _edit(request, form, title=f"Plan {plan}" if plan else "New plan", what="plan", hint=hint)


# --------------------------------------------------------------------------- orders


@staff_required
def orders(request):
    status, query = request.GET.get("status") or "", (request.GET.get("q") or "").strip()
    rows = Order.objects.select_related("account").prefetch_related("lines__plan__product")
    if status in Order.Status.values:
        rows = rows.filter(status=status)
    if query:
        rows = rows.filter(Q(ref__icontains=query) | Q(account__name__icontains=query) | Q(invoices__number__icontains=query)).distinct()
    return render(request, "backoffice/orders.html", {"orders": rows[:200], "statuses": Order.Status.choices, "current": status, "q": query})


@staff_required
def order(request, ref):
    order = get_object_or_404(Order.objects.select_related("account", "placed_by"), ref=ref)
    if request.method == "POST":
        return _order_action(request, order)
    audit("order.viewed", actor=request.user, actor_kind="operator", account=order.account, obj=order.ref)
    history = [{"event": e, "label": event_label(e.kind)} for e in order.payment_events.select_related("actor", "payment")]
    paid = sum((p.amount for p in order.payments.all() if p.status in ("succeeded", "partially_refunded")), Decimal("0"))
    return render(
        request,
        "backoffice/order.html",
        {
            "order": order,
            "lines": order.lines.select_related("plan__product", "subscription"),
            "payments": order.payments.all(),
            "history": history,
            "invoices": order.invoices.all(),
            "issues": order.issues.filter(resolved=False),
            "refundable": max(paid - order.refunded_total, Decimal("0")),
            "invoice": invoicing.invoice_for(order),
        },
    )


def _order_action(request, order):
    action, user = request.POST.get("action"), request.user
    try:
        if action == "mark_paid" and order.status == Order.Status.PENDING:
            payments.confirm_bank_payment(order, by=user)
            messages.success(request, "Marked as paid. The plan is active, the confirmation has gone out.")
        elif action == "cancel":
            payments.cancel_order(order, by=user)
            messages.success(request, "Order cancelled.")
        elif action == "refund":
            raw = (request.POST.get("amount") or "").strip().replace(",", ".")
            amount = Decimal(raw) if raw else None
            refund = payments.refund_order(order, amount=amount, reason=request.POST.get("reason", ""), by=user)
            note = " Transfer the money back by hand: a bank transfer cannot be refunded automatically." if refund.payment.provider == "bank_transfer" else ""
            messages.success(request, f"€{refund.amount} refunded, credit note issued." + note)
        elif action == "correct_invoice":
            invoice = get_object_or_404(Invoice, number=request.POST.get("number"), order=order)
            buyer = invoicing.buyer_from(
                company=request.POST.get("company", ""), name=request.POST.get("name", ""), street=request.POST.get("street", ""),
                postal_code=request.POST.get("postal_code", ""), city=request.POST.get("city", ""),
                country=request.POST.get("country", "") or "DE", vat_id=request.POST.get("vat_id", ""),
            )
            _void, replacement = invoicing.correct_invoice(invoice, buyer=buyer, reason=request.POST.get("reason", "") or "New invoice recipient")
            mails.invoice_document(order, replacement, replaced=invoice)
            audit("invoice.corrected", actor=user, actor_kind="operator", account=order.account, obj=replacement.number, data={"replaces": invoice.number})
            messages.success(request, f"Invoice {invoice.number} voided, {replacement.number} issued and sent.")
        elif action == "void_invoice":
            invoice = get_object_or_404(Invoice, number=request.POST.get("number"), order=order)
            void = invoicing.void_invoice(invoice, reason=request.POST.get("reason", "") or "Invoice withdrawn")
            mails.invoice_document(order, void)
            audit("invoice.voided", actor=user, actor_kind="operator", account=order.account, obj=void.number, data={"voids": invoice.number})
            messages.success(request, f"Invoice {invoice.number} voided by {void.number}.")
    except (payments.RefundError, invoicing.CorrectionError, PdfUnavailable) as exc:
        messages.error(request, str(exc))
    except InvalidOperation:
        messages.error(request, "Enter the amount as a number, for example 25.00.")
    return redirect("backoffice:order", order.ref)


# --------------------------------------------------------------------------- invoices


@staff_required
def invoices(request):
    today = timezone.localdate()
    try:
        year, month = int(request.GET.get("year") or today.year), int(request.GET.get("month") or today.month)
        date(year, month, 1)
    except ValueError:
        year, month = today.year, today.month
    rows = list(export.invoices_of(year, month))
    return render(
        request,
        "backoffice/invoices.html",
        {
            "invoices": rows,
            "year": year,
            "month": month,
            "net": sum((i.net_total for i in rows), Decimal("0")),
            "gross": sum((i.gross_total for i in rows), Decimal("0")),
            "year_net": invoicing.annual_turnover(year),
        },
    )


@staff_required
@pdf_view
def invoice_pdf(request, number):
    invoice = get_object_or_404(Invoice, number=number)
    audit("invoice.downloaded", actor=request.user, actor_kind="operator", account=invoice.account, obj=invoice.number)
    response = HttpResponse(invoice.read(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
    return response


@staff_required
def invoice_export(request, year, month):
    content, count = export.build_package(year, month)
    audit("invoice.exported", actor=request.user, actor_kind="operator", data={"month": f"{year}-{month:02d}", "documents": count})
    response = HttpResponse(content, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="cassilion-{year}-{month:02d}.zip"'
    return response


# --------------------------------------------------------------------------- payment matters, bank


@staff_required
def payment_issues(request):
    if request.method == "POST":
        issue = get_object_or_404(ReconciliationIssue, pk=request.POST.get("issue"), resolved=False)
        issue.resolved, issue.resolved_by = True, request.user
        issue.save(update_fields=["resolved", "resolved_by"])
        audit("payment.issue_resolved", actor=request.user, actor_kind="operator", obj=str(issue.pk), data={"kind": issue.kind})
        return redirect("backoffice:payment_issues")
    return render(
        request,
        "backoffice/payment_issues.html",
        {
            "issues": ReconciliationIssue.objects.filter(resolved=False).select_related("order"),
            "done": ReconciliationIssue.objects.filter(resolved=True).select_related("order", "resolved_by")[:30],
            "waiting": Order.objects.filter(status=Order.Status.PENDING, payment_method="bank_transfer").select_related("account")[:50],
        },
    )


@staff_required
def bank_import(request):
    report = None
    if request.method == "POST" and request.FILES.get("statement"):
        upload = request.FILES["statement"]
        if upload.size > 5 * 1024 * 1024:
            messages.error(request, "The file is larger than 5 MB.")
        else:
            try:
                report = payments.import_statement(upload.read(), by=request.user)
                audit(
                    "payment.statement_imported", actor=request.user, actor_kind="operator",
                    data={"matched": len(report.matched), "unmatched": len(report.unmatched), "problems": len(report.problems)},
                )
            except payments.StatementError as exc:
                messages.error(request, f"The file could not be read as a CAMT.053 or CSV statement: {exc}")
    return render(request, "backoffice/bank_import.html", {"report": report})


# --------------------------------------------------------------------------- payment accounts

SECRETS = {"stripe_secret_key": "Stripe secret key", "stripe_webhook_secret": "Stripe webhook signing secret", "paypal_secret": "PayPal secret"}


class PaymentSettingsForm(forms.ModelForm):
    methods = forms.MultipleChoiceField(
        label="Methods offered to customers",
        choices=[("stripe", "Card, Apple Pay, Google Pay (Stripe)"), ("paypal", "PayPal"), ("bank_transfer", "Bank transfer")],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="A method is only shown to customers once its account below is filled in.",
    )

    class Meta:
        model = PaymentSettings
        exclude = list(SECRETS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["methods"] = self.instance.method_list
        for name, text in SECRETS.items():
            stored = bool(getattr(self.instance, name))
            # A stored secret is never sent back to the browser. Empty = keep what is stored.
            self.fields[name] = forms.CharField(
                label=text,
                required=False,
                widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
                help_text="Stored. Leave empty to keep it; enter a new value to replace it." if stored else "Not set.",
            )
            self.fields[f"clear_{name}"] = forms.BooleanField(label=f"Remove the stored {text}", required=False)
        self.order_fields(
            [
                "methods", "stripe_secret_key", "clear_stripe_secret_key", "stripe_webhook_secret", "clear_stripe_webhook_secret",
                "paypal_client_id", "paypal_secret", "clear_paypal_secret", "paypal_webhook_id", "paypal_sandbox",
            ]
        )

    def clean_methods(self):
        return ",".join(self.cleaned_data["methods"])

    def clean_iban(self):
        return " ".join(self.cleaned_data["iban"].upper().split())

    def save(self, commit=True):
        obj = super().save(commit=False)
        for name in SECRETS:
            if self.cleaned_data.get(f"clear_{name}"):
                setattr(obj, name, "")
            elif self.cleaned_data.get(name):
                setattr(obj, name, self.cleaned_data[name].strip())
        obj.save()
        return obj


def _webhook_url(provider: str) -> str:
    return settings.CASSILION["SITE_URL"].rstrip("/") + reverse("billing:webhook", args=[provider])


@staff_required
def payment_settings(request):
    config = PaymentSettings.load()
    form = PaymentSettingsForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        secretive = set(SECRETS) | {f"clear_{n}" for n in SECRETS}
        changed = sorted(n for n in form.changed_data if n not in secretive or form.cleaned_data.get(n))
        form.save()
        # The audit line names the fields, never their values.
        audit("payment.settings_changed", actor=request.user, actor_kind="operator", data={"fields": changed})
        messages.success(request, "Payment settings saved.")
        return redirect("backoffice:payment_settings")
    return render(
        request,
        "backoffice/payment_settings.html",
        {
            "form": form,
            "state": {code: is_configured(code, config) for code in ("stripe", "paypal", "bank_transfer")},
            "stripe_mode": stripe.mode(config.stripe_secret_key) if config.stripe_secret_key else "",
            "stripe_hook": bool(config.stripe_webhook_secret),
            "paypal_hook": config.paypal_webhook_id,
            "hooks": {"stripe": _webhook_url("stripe"), "paypal": _webhook_url("paypal")},
            "sandbox": settings.CASSILION["SANDBOX_PAYMENTS"],
            "vat_id": settings.COMPANY["VAT_ID"],
        },
    )


@staff_required
@require_POST
def payment_check(request, provider):
    """Try the stored keys against the provider, or create our webhook there."""
    config, module = PaymentSettings.load(), {"stripe": stripe, "paypal": paypal}.get(provider)
    if module is None or not is_configured(provider, config):
        messages.error(request, "Enter and save the keys first.")
        return redirect("backoffice:payment_settings")
    try:
        if request.POST.get("action") == "webhook":
            hook = module.set_up_webhook(config, _webhook_url(provider))
            audit("payment.webhook_set_up", actor=request.user, actor_kind="operator", data={"provider": provider, "id": hook})
            messages.success(request, f"Webhook {hook} is set up at {provider.title()} and its secret is stored.")
        else:
            messages.success(request, f"Connection works: {module.check_connection(config)}.")
    except ProviderError as exc:
        messages.error(request, str(exc))
    return redirect("backoffice:payment_settings")
