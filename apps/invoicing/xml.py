"""ZUGFeRD / Factur-X (profile EN 16931) XML for an invoice. Taken over from the Tangosummer ticket shop;
added: invoices that are still to be paid (due date, bank account)."""

from __future__ import annotations

from decimal import Decimal
from xml.sax.saxutils import escape

NS = (
    'xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" '
    'xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100" '
    'xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"'
)


def _d(value) -> str:
    return f"{abs(Decimal(value)):.2f}"


def _date(value) -> str:
    return f'<udt:DateTimeString format="102">{value:%Y%m%d}</udt:DateTimeString>'


def _party(
    tag: str,
    lines: list[str],
    country: str,
    extra: str = "",
    post: str = "",
    street: str = "",
    city: str = "",
) -> str:
    name = escape(lines[0]) if lines else "-"
    return (
        f"<ram:{tag}><ram:Name>{name}</ram:Name><ram:PostalTradeAddress>"
        + (f"<ram:PostcodeCode>{escape(post)}</ram:PostcodeCode>" if post else "")
        + (f"<ram:LineOne>{escape(street)}</ram:LineOne>" if street else "")
        + (f"<ram:CityName>{escape(city)}</ram:CityName>" if city else "")
        + f"<ram:CountryID>{escape(country or 'DE')}</ram:CountryID></ram:PostalTradeAddress>{extra}</ram:{tag}>"
    )


def build(invoice_data: dict) -> bytes:
    """``invoice_data`` is the dict produced by :func:`apps.invoicing.services.compose`."""
    seller, buyer = invoice_data["seller"], invoice_data["buyer"]
    category = "E" if invoice_data["small_business"] else "S"
    credit = invoice_data["kind"] == "credit_note"
    items = []
    for n, line in enumerate(invoice_data["lines"], start=1):
        rate = Decimal(line["tax_rate"]) if not invoice_data["small_business"] else Decimal("0")
        items.append(
            f"<ram:IncludedSupplyChainTradeLineItem><ram:AssociatedDocumentLineDocument>"
            f"<ram:LineID>{n}</ram:LineID></ram:AssociatedDocumentLineDocument>"
            f"<ram:SpecifiedTradeProduct><ram:Name>{escape(line['description'])}</ram:Name></ram:SpecifiedTradeProduct>"
            f"<ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice><ram:ChargeAmount>{_d(line['net_unit'])}</ram:ChargeAmount>"
            f"</ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>"
            f'<ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="C62">{abs(int(line["quantity"]))}</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>'
            f"<ram:SpecifiedLineTradeSettlement><ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode>"
            f"<ram:CategoryCode>{category}</ram:CategoryCode><ram:RateApplicablePercent>{rate:.2f}</ram:RateApplicablePercent></ram:ApplicableTradeTax>"
            f"<ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>{_d(line['net_total'])}</ram:LineTotalAmount>"
            f"</ram:SpecifiedTradeSettlementLineMonetarySummation></ram:SpecifiedLineTradeSettlement></ram:IncludedSupplyChainTradeLineItem>"
        )

    taxes = []
    for entry in invoice_data["tax_breakdown"]:
        reason = (
            f"<ram:ExemptionReason>{escape(invoice_data['small_business_note'])}</ram:ExemptionReason>"
            if invoice_data["small_business"]
            else ""
        )
        taxes.append(
            f"<ram:ApplicableTradeTax><ram:CalculatedAmount>{_d(entry['tax'])}</ram:CalculatedAmount><ram:TypeCode>VAT</ram:TypeCode>"
            f"{reason}<ram:BasisAmount>{_d(entry['net'])}</ram:BasisAmount><ram:CategoryCode>{category}</ram:CategoryCode>"
            f"<ram:RateApplicablePercent>{Decimal(entry['rate']):.2f}</ram:RateApplicablePercent></ram:ApplicableTradeTax>"
        )

    registration = ""
    if seller.get("vat_id"):
        registration += f'<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{escape(seller["vat_id"])}</ram:ID></ram:SpecifiedTaxRegistration>'
    if seller.get("tax_number"):
        registration += f'<ram:SpecifiedTaxRegistration><ram:ID schemeID="FC">{escape(seller["tax_number"])}</ram:ID></ram:SpecifiedTaxRegistration>'

    note = escape(invoice_data["small_business_note"]) if invoice_data["small_business"] else ""
    reference = (
        f"<ram:InvoiceReferencedDocument><ram:IssuerAssignedID>{escape(invoice_data['refers_to'])}</ram:IssuerAssignedID></ram:InvoiceReferencedDocument>"
        if invoice_data.get("refers_to")
        else ""
    )
    due = bool(invoice_data.get("due_date")) and not credit
    means = ""
    if due and seller.get("iban"):
        means = (
            "<ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>58</ram:TypeCode>"
            f"<ram:PayeePartyCreditorFinancialAccount><ram:IBANID>{escape(seller['iban'].replace(' ', ''))}</ram:IBANID>"
            "</ram:PayeePartyCreditorFinancialAccount></ram:SpecifiedTradeSettlementPaymentMeans>"
        )
    terms = (
        f"<ram:SpecifiedTradePaymentTerms><ram:DueDateDateTime>{_date(invoice_data['due_date'])}</ram:DueDateDateTime></ram:SpecifiedTradePaymentTerms>"
        if due
        else ""
    )
    prepaid = not credit and not due
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><rsm:CrossIndustryInvoice {NS}>'
        "<rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter>"
        "<ram:ID>urn:cen.eu:en16931:2017</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter></rsm:ExchangedDocumentContext>"
        f"<rsm:ExchangedDocument><ram:ID>{escape(invoice_data['number'])}</ram:ID><ram:TypeCode>{'381' if credit else '380'}</ram:TypeCode>"
        f"<ram:IssueDateTime>{_date(invoice_data['issue_date'])}</ram:IssueDateTime>"
        + (
            f"<ram:IncludedNote><ram:Content>{note}</ram:Content></ram:IncludedNote>"
            if note
            else ""
        )
        + "</rsm:ExchangedDocument><rsm:SupplyChainTradeTransaction>"
        + "".join(items)
        + "<ram:ApplicableHeaderTradeAgreement>"
        + _party(
            "SellerTradeParty",
            [seller["name"]],
            seller.get("country", "DE"),
            registration,
            seller.get("postal_code", ""),
            seller.get("street", ""),
            seller.get("city", ""),
        )
        + _party(
            "BuyerTradeParty",
            buyer["lines"],
            buyer.get("country", "DE"),
            post=buyer.get("postal_code", ""),
            street=buyer.get("street", ""),
            city=buyer.get("city", ""),
        )
        + "</ram:ApplicableHeaderTradeAgreement><ram:ApplicableHeaderTradeDelivery><ram:ActualDeliverySupplyChainEvent>"
        f"<ram:OccurrenceDateTime>{_date(invoice_data['service_from'])}</ram:OccurrenceDateTime>"
        "</ram:ActualDeliverySupplyChainEvent></ram:ApplicableHeaderTradeDelivery><ram:ApplicableHeaderTradeSettlement>"
        f"<ram:PaymentReference>{escape(invoice_data['order_number'])}</ram:PaymentReference>"
        "<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>"
        + means
        + "".join(taxes)
        + terms
        + "<ram:SpecifiedTradeSettlementHeaderMonetarySummation>"
        f"<ram:LineTotalAmount>{_d(invoice_data['net_total'])}</ram:LineTotalAmount>"
        f"<ram:TaxBasisTotalAmount>{_d(invoice_data['net_total'])}</ram:TaxBasisTotalAmount>"
        f'<ram:TaxTotalAmount currencyID="EUR">{_d(invoice_data["tax_total"])}</ram:TaxTotalAmount>'
        f"<ram:GrandTotalAmount>{_d(invoice_data['gross_total'])}</ram:GrandTotalAmount>"
        + (f"<ram:TotalPrepaidAmount>{_d(invoice_data['gross_total'])}</ram:TotalPrepaidAmount>" if prepaid else "")
        + f"<ram:DuePayableAmount>{'0.00' if prepaid else _d(invoice_data['gross_total'])}</ram:DuePayableAmount>"
        "</ram:SpecifiedTradeSettlementHeaderMonetarySummation>"
        + reference  # schema order: the referenced invoice follows the monetary summation
        + "</ram:ApplicableHeaderTradeSettlement></rsm:SupplyChainTradeTransaction></rsm:CrossIndustryInvoice>"
    )
    return xml.encode("utf-8")
