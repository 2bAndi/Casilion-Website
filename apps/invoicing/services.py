"""Issue invoices and credit notes.

Taken over from the Tangosummer ticket shop. Every document is frozen when it is issued: the
data as printed is stored next to the PDF, which carries the machine-readable invoice inside
(ZUGFeRD / Factur-X, EN 16931). Nothing is ever changed; mistakes are corrected by a credit
note and a new invoice from the same number sequence.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.billing.providers import config as payment_config

from . import xml as facturx_xml
from .models import Invoice, InvoiceKind, InvoicePurpose, InvoiceSequence

CENT = Decimal("0.01")
SMALL_BUSINESS_NOTE = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."

LABELS = {
    "de": {
        "invoice": "Rechnung", "credit_note": "Gutschrift", "cancellation": "Stornorechnung", "correction": "Korrigierte Rechnung",
        "invoice_no": "Rechnungsnr.", "credit_note_no": "Gutschriftnr.", "cancellation_no": "Stornonr.",
        "date": "Datum", "service": "Leistungszeitraum", "order": "Bestellung", "customer": "Kundenkonto", "vat_id": "USt-IdNr.",
        "tax_no": "Steuernr.", "replaces": "Ersetzt Rechnung", "refers": "Bezug auf Rechnung",
        "description": "Beschreibung", "qty": "Menge", "unit": "Einzelpreis", "total": "Gesamt",
        "net_at": "Netto zu {rate} % USt: {net}", "sum": "Gesamtbetrag",
        "small_business": "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet (Kleinunternehmerregelung).",
        "reason": "Grund:", "paid": "Der Betrag ist bezahlt. Vielen Dank!",
        "due": "Bitte zahlen Sie {amount} bis zum {date}.", "transfer": "Überweisung an {holder}, IBAN {iban}{bic}, Verwendungszweck {ref}.",
        "online": "Online bezahlen: {url}",
        "refund": "Der Betrag wird auf dem ursprünglichen Zahlungsweg erstattet.",
        "void": "Storniert Rechnung {n} vollständig.", "void_note": "Es wird kein Geld zurückgezahlt, soweit die stornierte Rechnung nicht bezahlt war oder eine korrigierte Rechnung sie ersetzt.",
        "replaced": "Ersetzt Rechnung {n}, die storniert wurde.",
        "machine": "Dieses Dokument enthält maschinenlesbare Rechnungsdaten (ZUGFeRD / Factur-X, EN 16931).",
        "partial": "Teilerstattung zu Rechnung {n}", "director": "Geschäftsführer",
        "test": "TEST – keine Rechnung. Dieser Kauf lief über die Sandbox, es ist kein Geld geflossen.",
    },
    "en": {
        "invoice": "Invoice", "credit_note": "Credit note", "cancellation": "Cancellation invoice", "correction": "Corrected invoice",
        "invoice_no": "Invoice no.", "credit_note_no": "Credit note no.", "cancellation_no": "Cancellation no.",
        "date": "Date", "service": "Service period", "order": "Order", "customer": "Customer account", "vat_id": "VAT ID",
        "tax_no": "Tax no.", "replaces": "Replaces invoice", "refers": "Refers to invoice",
        "description": "Description", "qty": "Qty", "unit": "Unit price", "total": "Total",
        "net_at": "Net at {rate} % VAT: {net}", "sum": "Total",
        "small_business": "No VAT is charged pursuant to § 19 UStG (small business regulation).",
        "reason": "Reason:", "paid": "The amount has been paid. Thank you!",
        "due": "Please pay {amount} by {date}.", "transfer": "Bank transfer to {holder}, IBAN {iban}{bic}, reference {ref}.",
        "online": "Pay online: {url}",
        "refund": "The amount is refunded to the original means of payment.",
        "void": "Cancels invoice {n} in full.", "void_note": "No money is paid back where the cancelled invoice had not been paid or a corrected invoice replaces it.",
        "replaced": "Replaces invoice {n}, which has been cancelled.",
        "machine": "This document contains machine-readable invoice data (ZUGFeRD / Factur-X, EN 16931).",
        "partial": "Partial refund for invoice {n}", "director": "Managing director",
        "test": "TEST – not an invoice. This purchase went through the sandbox; no money moved.",
    },
}


class CorrectionError(Exception):
    pass


def labels(language: str) -> dict:
    return LABELS["de" if (language or "").startswith("de") else "en"]


def money(value, language: str = "en") -> str:
    text = f"{Decimal(value):,.2f}"
    if (language or "").startswith("de"):
        return text.replace(",", " ").replace(".", ",").replace(" ", ".") + " €"
    return "€" + text if not text.startswith("-") else "-€" + text[1:]


def _q(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def next_number(year: int, test: bool = False) -> str:
    """Gapless, per year. Must run inside the transaction that saves the invoice.

    Documents of test orders count on their own and are called TEST-…: trying the shop never
    uses up a number of the real sequence."""
    sequence, _created = InvoiceSequence.objects.select_for_update().get_or_create(year=year, test=test)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    prefix = "TEST" if test else payment_config().invoice_prefix
    return f"{prefix}-{year}-{sequence.last_number:05d}"


def invoice_for(order, number: str | None = None) -> Invoice | None:
    """The order's valid invoice: the latest one that has not been voided."""
    invoices = order.invoices.filter(kind=InvoiceKind.INVOICE)
    if number:
        return invoices.filter(number=number).first()
    return invoices.exclude(credit_notes__purpose=InvoicePurpose.CANCELLATION).order_by("-pk").first()


def is_voided(invoice: Invoice) -> bool:
    return invoice.credit_notes.filter(purpose=InvoicePurpose.CANCELLATION).exists()


def seller() -> dict:
    company, config = settings.COMPANY, payment_config()
    return {
        "name": company["NAME"],
        "street": company["STREET"],
        "postal_code": company["POSTAL_CODE"],
        "city": company["CITY"],
        "country": "DE",
        "tax_number": config.tax_number,
        "vat_id": company["VAT_ID"],
        "email": company["EMAIL"],
        "director": company["DIRECTOR"],
        "register": f"{company['REGISTER_COURT']} {company['REGISTER_NO']}",
        "iban": config.iban,
        "bic": config.bic,
        "bank": config.bank_name,
        "holder": config.bank_account_holder or company["NAME"],
    }


def buyer_from(*, company: str = "", name: str = "", extra: str = "", street: str = "", postal_code: str = "", city: str = "", country: str = "DE", vat_id: str = "") -> dict:
    """Recipient block as printed (and as the e-invoice XML reads it)."""
    lines = [company, name, extra, street, f"{postal_code} {city}".strip()]
    return {
        "lines": [line.strip() for line in lines if line and line.strip()],
        "country": (country or "DE").strip().upper()[:2],
        "postal_code": postal_code.strip(),
        "street": street.strip(),
        "city": city.strip(),
        "vat_id": vat_id.strip(),
    }


def buyer_of(account, user=None) -> dict:
    """The invoice recipient of an account as it stands now; frozen into the order when it is placed."""
    from apps.accounts.models import Address, TaxProfile

    # Read fresh: the order page has just saved the address.
    address, tax = Address.objects.filter(account=account).first(), TaxProfile.objects.filter(account=account).first()
    is_company = account.kind == "company"
    return buyer_from(
        company=account.name if is_company else "",
        name=(user.get_full_name() if user and is_company else account.name),
        extra=getattr(address, "line2", "") or "",
        street=getattr(address, "line1", "") or "",
        postal_code=getattr(address, "postal_code", "") or "",
        city=getattr(address, "city", "") or "",
        country=str(account.country or "DE"),
        vat_id=getattr(tax, "vat_id", "") or "",
    )


def _order_lines(order, sign: Decimal, language: str) -> list[dict]:
    lines = []
    for line in order.lines.all():
        description = line.description
        if line.period_from and line.period_to:
            fmt = "%d.%m.%Y" if language.startswith("de") else "%d %b %Y"
            description += f" ({line.period_from.strftime(fmt)} – {line.period_to.strftime(fmt)})"
        lines.append(
            {
                "description": description,
                "quantity": line.quantity,
                "unit_price": str(sign * line.unit_price),
                "gross_total": str(sign * line.total),
                "tax_rate": str(line.tax_rate),
            }
        )
    return lines


def _tax_breakdown(lines: list[dict], small_business: bool) -> tuple[list[dict], Decimal, Decimal, Decimal]:
    """Split final prices into net and tax per rate. Small businesses show no tax at all."""
    groups: dict[Decimal, Decimal] = defaultdict(Decimal)
    for line in lines:
        rate = Decimal("0") if small_business else Decimal(line["tax_rate"])
        groups[rate] += Decimal(line["gross_total"])
    breakdown, net_sum, tax_sum = [], Decimal("0"), Decimal("0")
    for rate, gross in sorted(groups.items()):
        tax = _q(gross * rate / (100 + rate)) if rate else Decimal("0.00")
        net = gross - tax
        breakdown.append({"rate": f"{rate.normalize():f}", "net": str(net), "tax": str(tax), "gross": str(gross)})
        net_sum, tax_sum = net_sum + net, tax_sum + tax
    return breakdown, net_sum, tax_sum, net_sum + tax_sum


def compose(order, kind: str, *, lines_override=None, refers_to: Invoice | None = None, reason: str = "", buyer: dict | None = None, small_business: bool | None = None, purpose: str = "", due_date=None) -> dict:
    """Everything that ends up on the document, as plain data (frozen into the invoice)."""
    config = payment_config()
    sign = Decimal("-1") if kind == InvoiceKind.CREDIT_NOTE else Decimal("1")
    small = config.small_business if small_business is None else small_business
    language = order.language or "en"
    lines = _order_lines(order, sign, language) if lines_override is None else lines_override
    for line in lines:
        rate = Decimal("0") if small else Decimal(line["tax_rate"])
        line["net_unit"] = str(_q(Decimal(line["unit_price"]) / (1 + rate / 100)))
        line["net_total"] = str(_q(Decimal(line["gross_total"]) / (1 + rate / 100)))
    breakdown, net, tax, gross = _tax_breakdown(lines, small)
    today = timezone.localdate()
    periods = [(l.period_from, l.period_to) for l in order.lines.all() if l.period_from and l.period_to]
    return {
        "kind": kind,
        "number": "",
        "issue_date": today,
        "due_date": due_date,
        "service_from": min((p[0] for p in periods), default=today),
        "service_to": max((p[1] for p in periods), default=today),
        "seller": seller(),
        "buyer": buyer or order.billing or buyer_of(order.account, order.placed_by),
        "lines": lines,
        "net_total": net,
        "tax_total": tax,
        "gross_total": gross,
        "tax_breakdown": breakdown,
        "small_business": small,
        "small_business_note": SMALL_BUSINESS_NOTE,
        "refers_to": refers_to.number if refers_to else "",
        "reason": reason,
        "purpose": purpose,
        "order_number": order.ref,
        "is_test": order.is_test,
        "account_name": order.account.name,
        "payment_method": order.payment_method,
        "pay_url": settings.CASSILION["SITE_URL"].rstrip("/") + reverse("billing:order", args=[order.ref]),
    }


def render_pdf(data: dict, language: str) -> bytes:
    """HTML -> PDF/A-3 with the machine-readable invoice embedded (ZUGFeRD / Factur-X)."""
    from facturx import generate_from_binary

    from apps.core.pdf import html_to_pdf

    text = labels(language)
    view = {
        **data,
        "lines": [{**line, "unit_price": money(line["unit_price"], language), "gross_total": money(line["gross_total"], language)} for line in data["lines"]],
        "tax_lines": [
            {"text": text["net_at"].format(rate=row["rate"], net=money(row["net"], language)), "tax": money(row["tax"], language)}
            for row in data["tax_breakdown"]
        ],
        "gross": money(data["gross_total"], language),
    }
    fmt = "%d.%m.%Y" if language.startswith("de") else "%d %B %Y"
    notes = []
    if data["purpose"] == InvoicePurpose.CANCELLATION:
        notes += [text["void"].format(n=data["refers_to"]), text["void_note"]]
    elif data["kind"] == InvoiceKind.CREDIT_NOTE:
        notes.append(text["refund"])
    elif data["due_date"]:
        notes.append(text["due"].format(amount=money(abs(data["gross_total"]), language), date=data["due_date"].strftime(fmt)))
        s = data["seller"]
        if s["iban"]:
            notes.append(text["transfer"].format(holder=s["holder"], iban=s["iban"], bic=f", BIC {s['bic']}" if s["bic"] else "", ref=data["order_number"]))
        notes.append(text["online"].format(url=data["pay_url"]))
    else:
        notes.append(text["paid"])
    if data["purpose"] == InvoicePurpose.CORRECTION:
        notes.insert(0, text["replaced"].format(n=data["refers_to"]))
    html = render_to_string("invoicing/invoice.html", {"inv": view, "t": text, "language": language, "notes": notes, "fmt": "d.m.Y" if language.startswith("de") else "j F Y"})
    pdf = html_to_pdf(html, pdf_variant="pdf/a-3b")
    return generate_from_binary(pdf, facturx_xml.build(data), flavor="factur-x", level="en16931", check_xsd=True)


def _create(order, kind: str, **options) -> Invoice:
    with transaction.atomic():
        data = compose(order, kind, **options)
        data["number"] = next_number(data["issue_date"].year, test=order.is_test)
        language = order.language or "en"
        pdf = render_pdf(data, language)
        refers_to = options.get("refers_to")
        invoice = Invoice(
            number=data["number"],
            kind=kind,
            is_test=order.is_test,
            order=order,
            account=order.account,
            refers_to=refers_to,
            purpose=options.get("purpose", ""),
            reason=options.get("reason", "")[:300],
            issue_date=data["issue_date"],
            due_date=data["due_date"],
            service_from=data["service_from"],
            service_to=data["service_to"],
            language=language,
            seller=data["seller"],
            recipient=data["buyer"],
            lines=data["lines"],
            net_total=data["net_total"],
            tax_total=data["tax_total"],
            gross_total=data["gross_total"],
            tax_breakdown=data["tax_breakdown"],
            small_business=data["small_business"],
            payment_method=order.payment_method,
            pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        )
        invoice.pdf.save(f"{data['number']}.pdf", ContentFile(pdf), save=False)
        invoice.save()
    from apps.audit.log import audit

    audit("invoice.issued", account=order.account, obj=invoice.number, data={"kind": kind, "order": order.ref, "gross": str(invoice.gross_total)})
    return invoice


def issue_invoice(order, *, due_date=None) -> Invoice:
    """One valid invoice per order (idempotent). With ``due_date`` it is an invoice still to be paid."""
    return invoice_for(order) or _create(order, InvoiceKind.INVOICE, due_date=due_date)


def _copy_lines(lines: list[dict], sign: Decimal) -> list[dict]:
    copied = []
    for line in lines:
        row = {key: line.get(key) for key in ("description", "quantity", "unit_price", "gross_total", "tax_rate")}
        row["unit_price"] = str(sign * Decimal(row["unit_price"]))
        row["gross_total"] = str(sign * Decimal(row["gross_total"]))
        copied.append(row)
    return copied


def issue_credit_note(invoice: Invoice, amount: Decimal, *, reason: str = "", full: bool = False) -> Invoice:
    """Correct an invoice by a credit note from the same number sequence."""
    order = invoice.order
    common = {"refers_to": invoice, "reason": reason, "buyer": invoice.recipient, "small_business": invoice.small_business}
    if full and amount == invoice.gross_total:
        return _create(order, InvoiceKind.CREDIT_NOTE, lines_override=_copy_lines(invoice.lines, Decimal("-1")), **common)
    # A part of the amount goes back: one line, taxed like the invoice it belongs to.
    rate = invoice.lines[0]["tax_rate"] if invoice.lines else "0"
    line = {
        "description": labels(invoice.language)["partial"].format(n=invoice.number),
        "quantity": 1,
        "unit_price": str(-amount),
        "gross_total": str(-amount),
        "tax_rate": rate,
    }
    return _create(order, InvoiceKind.CREDIT_NOTE, lines_override=[line], **common)


def void_invoice(invoice: Invoice, *, reason: str) -> Invoice:
    """Void an invoice in full by a cancellation credit note (it was never owed, or is replaced)."""
    if invoice.kind != InvoiceKind.INVOICE:
        raise CorrectionError("Only invoices can be voided, not credit notes.")
    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if is_voided(locked):
            raise CorrectionError("This invoice has already been voided.")
        return _create(
            invoice.order,
            InvoiceKind.CREDIT_NOTE,
            lines_override=_copy_lines(invoice.lines, Decimal("-1")),
            refers_to=invoice,
            reason=reason,
            buyer=invoice.recipient,
            small_business=invoice.small_business,
            purpose=InvoicePurpose.CANCELLATION,
        )


def correct_invoice(invoice: Invoice, *, buyer: dict, reason: str):
    """A new recipient on an issued invoice ("company address, please").

    Invoices are never changed. The old one is voided by a credit note over its full amount and
    a new invoice with the same lines and the new recipient replaces it. Both take the next
    numbers of the same sequence, in one transaction. Returns (credit note, new invoice)."""
    if not buyer.get("lines"):
        raise CorrectionError("Please enter the new recipient.")
    with transaction.atomic():
        void = void_invoice(invoice, reason=reason)
        replacement = _create(
            invoice.order,
            InvoiceKind.INVOICE,
            lines_override=_copy_lines(invoice.lines, Decimal("1")),
            refers_to=invoice,
            reason=reason,
            buyer=buyer,
            small_business=invoice.small_business,
            purpose=InvoicePurpose.CORRECTION,
            due_date=invoice.due_date if invoice.order.status == "pending" else None,
        )
    return void, replacement


def annual_turnover(year: int) -> Decimal:
    """Net revenue booked (invoices minus credit notes) in a calendar year."""
    return sum((invoice.net_total for invoice in Invoice.objects.filter(issue_date__year=year, is_test=False)), Decimal("0"))
