"""Hand-over to the tax adviser: journal CSV, DATEV booking batch, ZIP of all PDFs.
Taken over from the Tangosummer ticket shop."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from django.conf import settings

from apps.billing.models import COLLECTED, Payment
from apps.billing.providers import config as payment_config

from .models import Invoice

CLEARING_METHODS = {"stripe", "paypal"}


def cell(value) -> str:
    """Text for a CSV cell that a spreadsheet will not run as a formula."""
    text = str(value or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return start, end


def invoices_of(year: int, month: int):
    start, end = month_bounds(year, month)
    return Invoice.objects.filter(issue_date__gte=start, issue_date__lt=end).select_related("order", "refers_to").order_by("number")


def _de(value) -> str:
    return f"{Decimal(value):.2f}".replace(".", ",")


def journal_csv(invoices) -> str:
    """Journal for the tax office, German headers and decimal comma, one row per document."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "Nummer",
            "Art",
            "Datum",
            "Leistungsdatum",
            "Kunde",
            "Bestellung",
            "Netto",
            "Steuer",
            "Brutto",
            "Steuersatz",
            "Zahlweg",
            "Bezug",
        ]
    )
    for inv in invoices:
        rates = ", ".join(f"{Decimal(t['rate']):.0f}%" for t in inv.tax_breakdown) or "0%"
        writer.writerow(
            [
                inv.number,
                "Gutschrift" if inv.is_credit_note else "Rechnung",
                inv.issue_date.strftime("%d.%m.%Y"),
                inv.service_from.strftime("%d.%m.%Y"),
                cell(inv.recipient["lines"][0] if inv.recipient["lines"] else ""),
                inv.order.ref,
                _de(inv.net_total),
                _de(inv.tax_total),
                _de(inv.gross_total),
                rates,
                inv.payment_method,
                inv.refers_to.number if inv.refers_to_id else "",
            ]
        )
    return out.getvalue()


def datev_csv(invoices, year: int, month: int) -> str:
    """DATEV Buchungsstapel (EXTF 700, Buchungsstapel v13): payment received booked to revenue."""
    organizer = payment_config()
    start, end = month_bounds(year, month)
    now = timezone.now().strftime("%Y%m%d%H%M%S") + "000"
    header = [
        "EXTF",
        "700",
        "21",
        "Buchungsstapel",
        "13",
        now,
        "",
        "RE",
        "",
        "",
        organizer.datev_consultant_number or "0",
        organizer.datev_client_number or "0",
        f"{year}0101",
        "4",
        start.strftime("%Y%m%d"),
        (end.replace(day=1) - date.resolution).strftime("%Y%m%d"),
        f"Cassilion {year}-{month:02d}",
        "",
        "1",
        "0",
        "0",
        "EUR",
        "",
        "",
        "",
        "",
    ]
    columns = [
        "Umsatz (ohne Soll/Haben-Kz)",
        "Soll/Haben-Kennzeichen",
        "WKZ Umsatz",
        "Kurs",
        "Basis-Umsatz",
        "WKZ Basis-Umsatz",
        "Konto",
        "Gegenkonto (ohne BU-Schlüssel)",
        "BU-Schlüssel",
        "Belegdatum",
        "Belegfeld 1",
        "Belegfeld 2",
        "Skonto",
        "Buchungstext",
    ]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_NONNUMERIC)
    writer.writerow(header)
    writer.writerow(columns)
    for inv in invoices:
        method = inv.payment_method
        money_account = organizer.datev_clearing_account if method in CLEARING_METHODS else organizer.datev_bank_account
        text = cell(f"{inv.order.ref} {(inv.recipient['lines'] or [''])[0]}"[:60])
        writer.writerow(
            [
                _de(abs(inv.gross_total)),
                "H" if inv.is_credit_note else "S",
                "EUR",
                "",
                "",
                "",
                money_account,
                organizer.datev_revenue_account,
                "",
                inv.issue_date.strftime("%d%m"),
                inv.number,
                "",
                "",
                text,
            ]
        )
    return out.getvalue()


def payments_csv(year: int, month: int) -> str:
    """Every payment collected in the month, to check against the providers' payout reports."""
    start, end = month_bounds(year, month)
    tz = timezone.get_current_timezone()
    rows = Payment.objects.filter(
        status__in=COLLECTED,
        created_at__gte=datetime.combine(start, datetime.min.time(), tz),
        created_at__lt=datetime.combine(end, datetime.min.time(), tz),
    ).select_related("order").order_by("created_at")
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Zahlweg", "Referenz", "Bestellung", "Betrag", "Status", "Zeitpunkt"])
    for r in rows:
        writer.writerow([r.provider, cell(r.provider_id), r.order.ref, _de(r.amount), r.status, r.created_at.isoformat()])
    return out.getvalue()


def build_package(year: int, month: int) -> tuple[bytes, int]:
    """Everything the tax adviser needs for one month, as one ZIP file."""
    invoices = list(invoices_of(year, month))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for inv in invoices:
            archive.writestr(f"belege/{inv.number}.pdf", inv.read())
        archive.writestr("journal.csv", "\ufeff" + journal_csv(invoices))
        archive.writestr("EXTF_Buchungsstapel.csv", datev_csv(invoices, year, month))
        archive.writestr("zahlungen.csv", "\ufeff" + payments_csv(year, month))
        archive.writestr(
            "LIESMICH.txt",
            f"Monatspaket {year}-{month:02d} · {settings.COMPANY['NAME']}\r\n"
            f"{len(invoices)} Belege (Rechnungen und Gutschriften), Journal, DATEV-Buchungsstapel, Zahlungen.\r\n",
        )
    return buffer.getvalue(), len(invoices)
