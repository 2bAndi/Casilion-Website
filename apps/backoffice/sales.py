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
    if status == "test":
        rows = rows.filter(is_test=True)
    elif status in Order.Status.values:
        rows = rows.filter(status=status)
    if query:
        rows = rows.filter(Q(ref__icontains=query) | Q(account__name__icontains=query) | Q(invoices__number__icontains=query)).distinct()
    return render(request, "backoffice/orders.html", {"orders": rows[:200], "statuses": [*Order.Status.choices, ("test", "Test orders (sandbox)")], "current": status, "q": query})


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
    tests = Invoice.objects.filter(is_test=True, issue_date__year=year, issue_date__month=month).count()
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
            "tests": tests,
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


# --------------------------------------------------------------------------- payment settings

SECRETS = {"stripe_secret_key": "Secret key", "stripe_webhook_secret": "Webhook signing secret", "paypal_secret": "Secret"}

# One panel per subject, each saved on its own. "offer": the method can be switched on and off for customers.
SECTIONS = {
    "stripe": {
        "title": "Stripe",
        "intro": "Cards, Apple Pay and Google Pay on Stripe's own checkout page.",
        "fields": ["stripe_secret_key", "stripe_webhook_secret"],
        "offer": "Offer card payment to customers",
    },
    "paypal": {
        "title": "PayPal",
        "intro": "The REST app from the PayPal developer dashboard. Start with PayPal's test system, then switch to live.",
        "fields": ["paypal_client_id", "paypal_secret", "paypal_webhook_id", "paypal_sandbox"],
        "offer": "Offer PayPal to customers",
    },
    "bank_transfer": {
        "title": "Bank transfer",
        "intro": "The account customers transfer to. It is printed on the order page, in the mails and on invoices that are still to be paid.",
        "fields": ["bank_account_holder", "iban", "bic", "bank_name"],
        "offer": "Offer bank transfer to customers",
    },
    "sandbox": {
        "title": "Sandbox (test purchases)",
        "intro": "A stand-in checkout for trying the shop. No money moves. What is bought there is marked as a test, gets TEST documents "
        "instead of invoices, stays out of the books and can be deleted below.",
        "fields": ["sandbox_enabled", "sandbox_testers"],
    },
    "invoices": {
        "title": "Invoices and tax",
        "intro": "What an invoice needs besides the company data from the server configuration.",
        "fields": ["invoice_prefix", "tax_number", "vat_rate", "small_business"],
    },
    "accounting": {
        "title": "Alerts and tax adviser",
        "intro": "Where payment alerts go, and the account numbers for the DATEV booking batch in the month export.",
        "fields": ["finance_email", "datev_consultant_number", "datev_client_number", "datev_revenue_account", "datev_bank_account", "datev_clearing_account"],
    },
}


class SectionForm(forms.ModelForm):
    """One section of the payment settings. A stored secret is never sent back to the browser:
    an empty field keeps what is stored, a new value replaces it."""

    def __init__(self, *args, key: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.key, spec = key, SECTIONS[key]
        order = []
        if spec.get("offer"):
            self.fields["offer"] = forms.BooleanField(label=spec["offer"], required=False, initial=key in self.instance.method_list)
            order.append("offer")
        for name in spec["fields"]:
            order.append(name)
            if name in SECRETS:
                stored = bool(getattr(self.instance, name))
                self.fields[name] = forms.CharField(
                    label=SECRETS[name],
                    required=False,
                    widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
                    help_text="Stored. Leave empty to keep it; enter a new value to replace it." if stored else "Not set.",
                )
                if stored:
                    self.fields[f"clear_{name}"] = forms.BooleanField(label=f"Remove the stored {SECRETS[name].lower()}", required=False)
                    order.append(f"clear_{name}")
        if "sandbox_testers" in self.fields:
            self.fields["sandbox_testers"].widget = forms.Textarea(attrs={"rows": 3})
        self.order_fields(order)

    def clean_iban(self):
        return " ".join(self.cleaned_data["iban"].upper().split())

    def changed(self) -> list[str]:
        """Names of what this save changes, for the audit line. Never the values."""
        return sorted(n for n in self.changed_data if not (n in SECRETS or n.startswith("clear_")) or self.cleaned_data.get(n))

    def save(self, commit=True):
        obj = super().save(commit=False)
        touched = [n for n in SECTIONS[self.key]["fields"] if n not in SECRETS]
        for name in SECRETS:
            if self.cleaned_data.get(f"clear_{name}"):
                setattr(obj, name, "")
                touched.append(name)
            elif self.cleaned_data.get(name):
                setattr(obj, name, self.cleaned_data[name].strip())
                touched.append(name)
        if "offer" in self.fields:
            methods = [m for m in obj.method_list if m != self.key]
            if self.cleaned_data["offer"]:
                methods.append(self.key)
            obj.methods = ",".join(m for m in ("stripe", "paypal", "bank_transfer") if m in methods)
            touched.append("methods")
        obj.save(update_fields=touched)
        return obj


def _section_form(key: str, data, config) -> SectionForm:
    plain = [name for name in SECTIONS[key]["fields"] if name not in SECRETS]
    form_class = forms.modelform_factory(PaymentSettings, form=SectionForm, fields=plain)
    return form_class(data, instance=config, prefix=key, key=key)


def _settings_url(tab: str) -> str:
    return reverse("backoffice:payment_settings") + f"?tab={tab}"


def _webhook_url(provider: str) -> str:
    return settings.CASSILION["SITE_URL"].rstrip("/") + reverse("billing:webhook", args=[provider])


@staff_required
def payment_settings(request):
    config = PaymentSettings.load()
    posted = request.POST.get("section") if request.method == "POST" else None
    section_forms = {key: _section_form(key, request.POST if key == posted else None, config) for key in SECTIONS}
    form = section_forms.get(posted)
    if form is not None and form.is_valid():
        changed = form.changed()
        form.save()
        # The audit line names who changed which fields, never their values.
        audit("payment.settings_changed", actor=request.user, actor_kind="operator", obj=posted, data={"fields": changed})
        messages.success(request, f"{SECTIONS[posted]['title']}: saved.")
        return redirect(_settings_url(posted))
    state = {code: is_configured(code, config) for code in ("stripe", "paypal", "bank_transfer", "sandbox")}
    offered = config.method_list
    sections = []
    for key, spec in SECTIONS.items():
        if key in ("stripe", "paypal", "bank_transfer"):
            status = ("offered", "ok") if state[key] and key in offered else ("set up, not offered", "quiet") if state[key] else ("not set up", "quiet")
        elif key == "sandbox":
            status = ("on", "action") if state["sandbox"] else ("off", "quiet")
        else:
            status = None
        sections.append({"key": key, "title": spec["title"], "intro": spec["intro"], "form": section_forms[key], "status": status})
    # One tab at a time: the one just saved, the one asked for, else the first.
    tab = posted if posted in SECTIONS else request.GET.get("tab")
    tab = tab if tab in SECTIONS else next(iter(SECTIONS))
    return render(
        request,
        "backoffice/payment_settings.html",
        {
            "sections": sections,
            "section": next(s for s in sections if s["key"] == tab),
            "state": state,
            "stripe_mode": stripe.mode(config.stripe_secret_key) if config.stripe_secret_key else "",
            "stripe_hook": bool(config.stripe_webhook_secret),
            "paypal_hook": config.paypal_webhook_id,
            "hooks": {"stripe": _webhook_url("stripe"), "paypal": _webhook_url("paypal")},
            "sandbox_open": state["sandbox"] and not config.tester_list,
            "testers": config.tester_list,
            "test_orders": payments.test_order_count(),
            "vat_id": settings.COMPANY["VAT_ID"],
            "tax_number": config.tax_number,
        },
    )


@staff_required
@require_POST
def payment_check(request, provider):
    """Try the stored keys against the provider, or create our webhook there."""
    config, module = PaymentSettings.load(), {"stripe": stripe, "paypal": paypal}.get(provider)
    if module is None or not is_configured(provider, config):
        messages.error(request, "Enter and save the keys first.")
        return redirect(_settings_url(provider))
    try:
        if request.POST.get("action") == "webhook":
            hook = module.set_up_webhook(config, _webhook_url(provider))
            audit("payment.webhook_set_up", actor=request.user, actor_kind="operator", data={"provider": provider, "id": hook})
            messages.success(request, f"Webhook {hook} is set up at {SECTIONS[provider]['title']} and stored here.")
        else:
            messages.success(request, f"Connection works: {module.check_connection(config)}.")
    except ProviderError as exc:
        messages.error(request, str(exc))
    return redirect(_settings_url(provider))


@staff_required
@require_POST
def purge_test_orders(request):
    """Delete what was bought through the sandbox. Asked for by a person, with a tick to confirm."""
    if request.POST.get("confirm") != "on":
        messages.error(request, "Tick the box to confirm. Nothing was deleted.")
    else:
        try:
            count = payments.purge_test_orders(by=request.user)
            messages.success(request, f"Deleted: {count['orders']} test order(s), {count['documents']} TEST document(s), {count['plans']} test plan(s).")
        except payments.PurgeError as exc:
            messages.error(request, str(exc))
    return redirect(_settings_url("sandbox"))
