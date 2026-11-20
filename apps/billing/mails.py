"""The mails about orders and money, in German or English by the language of the order."""
from django.conf import settings
from django.core.mail import EmailMessage


def _de(order) -> bool:
    return (order.language or "").startswith("de")


def _name(order) -> str:
    return order.placed_by.get_short_name() if order.placed_by else order.account.name


def _site() -> str:
    return settings.CASSILION["SITE_URL"].rstrip("/")


def _order_url(order) -> str:
    from django.urls import reverse

    return _site() + reverse("billing:order", args=[order.ref])


def _money(value, order) -> str:
    from apps.invoicing.services import money

    return money(value, order.language)


def _date(value, order) -> str:
    return value.strftime("%d.%m.%Y" if _de(order) else "%d %B %Y")


def _send(order, subject: str, lines: list[str], attachments=()) -> None:
    to = order.email
    if not to:
        return
    company = settings.COMPANY
    lines = [*lines, "", "Mit freundlichen Grüßen" if _de(order) else "Kind regards", company["NAME"], f"{company['STREET']}, {company['POSTAL_CODE']} {company['CITY']}", company["EMAIL"]]
    message = EmailMessage(f"[Cassilion] {subject}", "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [to], reply_to=[company["EMAIL"]])
    for name, content, mimetype in attachments:
        message.attach(name, content, mimetype)
    message.send(fail_silently=True)


def _pdf(invoice):
    return [(f"{invoice.number}.pdf", invoice.read(), "application/pdf")] if invoice is not None else []


def _plan(order) -> str:
    line = order.first_line
    return line.description if line else ""


def _bank_lines(order, config) -> list[str]:
    if _de(order):
        return [
            f"Empfänger: {config.bank_account_holder or settings.COMPANY['NAME']}",
            f"IBAN: {config.iban}" + (f"   BIC: {config.bic}" if config.bic else ""),
            f"Betrag: {_money(order.total, order)}",
            f"Verwendungszweck: {order.ref}",
        ]
    return [
        f"Payee: {config.bank_account_holder or settings.COMPANY['NAME']}",
        f"IBAN: {config.iban}" + (f"   BIC: {config.bic}" if config.bic else ""),
        f"Amount: {_money(order.total, order)}",
        f"Reference: {order.ref}",
    ]


def order_confirmation(order, period_end, invoice=None) -> None:
    """The confirmation of the contract. It carries the invoice, and the terms and the withdrawal
    policy as files: German consumer law wants them on a durable medium, and a link is not one."""
    from apps.core import legal  # imported here: core imports billing for the home page

    lang = "de" if _de(order) else "en"
    renewal = order.kind == "renewal"
    if lang == "de":
        lines = [
            f"Guten Tag {_name(order)},",
            "",
            "vielen Dank, Ihre Zahlung ist eingegangen. Der Vertrag läuft weiter:" if renewal else "vielen Dank für Ihre Bestellung. Wir bestätigen den Vertrag:",
            "",
            f"Bestellnummer: {order.ref}",
            f"Leistung: {_plan(order)}",
            f"Laufzeit: zwölf Monate bis {_date(period_end, order)}" if period_end else "",
            f"Preis: {_money(order.total, order)} pro Jahr, einschließlich Umsatzsteuer",
            f"Kundenkonto: {order.account.name}",
            "",
            "Der Tarif ist freigeschaltet. Eine neue Lizenzdatei liegt in Ihrem Kundenkonto bereit." + (" Die Rechnung finden Sie im Anhang." if invoice else ""),
        ]
        if order.early_start_requested_at:
            lines += [
                "",
                "Sie haben bei der Bestellung ausdrücklich verlangt, dass wir mit dem Support vor Ablauf der "
                "Widerrufsfrist beginnen. Widerrufen Sie den Vertrag, zahlen Sie für die bis dahin erbrachte Leistung "
                "einen anteiligen Betrag.",
            ]
        if not renewal:
            lines += [
                "",
                "Im Anhang finden Sie unsere Allgemeinen Geschäftsbedingungen und die Widerrufsbelehrung mit dem Muster-Widerrufsformular.",
                f"Vertrag widerrufen: {_site()}/legal/withdraw/?lang=de",
            ]
        lines += [f"Verträge hier kündigen: {_site()}/legal/cancel/?lang=de"]
        subject = f"Zahlungseingang {order.ref}" if renewal else f"Bestellbestätigung {order.ref}"
    else:
        lines = [
            f"Hello {_name(order)},",
            "",
            "Thank you, your payment has arrived. The contract continues:" if renewal else "Thank you for your order. We confirm the contract:",
            "",
            f"Order number: {order.ref}",
            f"Service: {_plan(order)}",
            f"Term: twelve months until {_date(period_end, order)}" if period_end else "",
            f"Price: {_money(order.total, order)} per year, including VAT",
            f"Customer account: {order.account.name}",
            "",
            "The plan is active. A new license file is ready in your account." + (" The invoice is attached." if invoice else ""),
        ]
        if order.early_start_requested_at:
            lines += [
                "",
                "When ordering you expressly asked us to start support before the withdrawal period ends. If you "
                "withdraw from the contract, you pay a proportionate amount for what has been provided until then.",
            ]
        if not renewal:
            lines += [
                "",
                "Attached are our terms and conditions and the withdrawal policy with the model withdrawal form.",
                f"Withdraw from contract: {_site()}/legal/withdraw/?lang=en",
            ]
        lines += [f"Cancel contracts here: {_site()}/legal/cancel/?lang=en"]
        subject = f"Payment received {order.ref}" if renewal else f"Order confirmation {order.ref}"
    attachments = _pdf(invoice) + ([] if renewal else list(legal.documents(lang)))
    _send(order, subject, lines, attachments)


def bank_transfer_instructions(order, config) -> None:
    if _de(order):
        lines = [
            f"Guten Tag {_name(order)},",
            "",
            f"vielen Dank für Ihre Bestellung {order.ref}: {_plan(order)}.",
            "Bitte überweisen Sie den Betrag" + (f" bis zum {_date(order.due_date, order)}" if order.due_date else "") + ":",
            "",
            *_bank_lines(order, config),
            "",
            "Sobald das Geld eingegangen ist, schalten wir den Tarif frei und senden Ihnen Bestätigung und Rechnung.",
            f"Stand der Bestellung: {_order_url(order)}",
        ]
        subject = f"Ihre Bestellung {order.ref}: Zahlung per Überweisung"
    else:
        lines = [
            f"Hello {_name(order)},",
            "",
            f"Thank you for your order {order.ref}: {_plan(order)}.",
            "Please transfer the amount" + (f" by {_date(order.due_date, order)}" if order.due_date else "") + ":",
            "",
            *_bank_lines(order, config),
            "",
            "As soon as the money has arrived we activate the plan and send you the confirmation and the invoice.",
            f"Status of the order: {_order_url(order)}",
        ]
        subject = f"Your order {order.ref}: payment by bank transfer"
    _send(order, subject, lines)


def renewal_notice(order, invoice, config, period_end, reminder: bool = False) -> None:
    """The invoice for the next term, sent before the current one ends (and once more when it is overdue)."""
    if _de(order):
        lines = [
            f"Guten Tag {_name(order)},",
            "",
            (f"die Rechnung für die Verlängerung Ihres Tarifs ist noch offen. Die bezahlte Laufzeit endete am {_date(period_end, order)}." if reminder
             else f"Ihr Tarif läuft am {_date(period_end, order)} in die nächsten zwölf Monate. Im Anhang finden Sie die Rechnung dafür."),
            "",
            f"Leistung: {_plan(order)}",
            f"Betrag: {_money(order.total, order)}, fällig am {_date(order.due_date, order)}",
            f"Online bezahlen: {_order_url(order)}",
        ]
        if config.iban:
            lines += ["", "Oder per Überweisung:", *_bank_lines(order, config)]
        lines += [
            "",
            (f"Ohne Zahlungseingang endet der Support nach {settings.CASSILION['GRACE_DAYS']} Tagen; die Software bleibt nutzbar." if reminder
             else "Wenn Sie nicht verlängern möchten, kündigen Sie bitte vor dem Ende der Laufzeit in Ihrem Kundenkonto. Die Rechnung wird dann storniert."),
            f"Verträge hier kündigen: {_site()}/legal/cancel/?lang=de",
        ]
        subject = f"Zahlungserinnerung {order.ref}" if reminder else f"Verlängerung Ihres Tarifs: Rechnung {invoice.number if invoice else order.ref}"
    else:
        lines = [
            f"Hello {_name(order)},",
            "",
            (f"The invoice for the renewal of your plan is still open. The paid term ended on {_date(period_end, order)}." if reminder
             else f"Your plan continues into the next twelve months on {_date(period_end, order)}. The invoice for them is attached."),
            "",
            f"Service: {_plan(order)}",
            f"Amount: {_money(order.total, order)}, due on {_date(order.due_date, order)}",
            f"Pay online: {_order_url(order)}",
        ]
        if config.iban:
            lines += ["", "Or by bank transfer:", *_bank_lines(order, config)]
        lines += [
            "",
            (f"Without payment, support ends after {settings.CASSILION['GRACE_DAYS']} days; the software stays usable." if reminder
             else "If you do not want to renew, please cancel in your customer account before the term ends. The invoice is then cancelled."),
            f"Cancel contracts here: {_site()}/legal/cancel/?lang=en",
        ]
        subject = f"Payment reminder {order.ref}" if reminder else f"Renewal of your plan: invoice {invoice.number if invoice else order.ref}"
    _send(order, subject, lines, _pdf(invoice))


def refund_notice(order, refund, credit_note=None, manual: bool = False) -> None:
    if _de(order):
        lines = [
            f"Guten Tag {_name(order)},",
            "",
            f"wir erstatten Ihnen {_money(refund.amount, order)} zur Bestellung {order.ref}.",
            ("Der Betrag wird auf das Konto überwiesen, von dem die Zahlung kam." if manual else "Der Betrag geht auf dem ursprünglichen Zahlungsweg zurück; das dauert meist einige Tage."),
        ]
        if credit_note:
            lines += ["Die Gutschrift finden Sie im Anhang."]
        subject = f"Erstattung zur Bestellung {order.ref}"
    else:
        lines = [
            f"Hello {_name(order)},",
            "",
            f"We are refunding {_money(refund.amount, order)} for order {order.ref}.",
            ("The amount is transferred to the account the payment came from." if manual else "The amount goes back to the original means of payment; this usually takes a few days."),
        ]
        if credit_note:
            lines += ["The credit note is attached."]
        subject = f"Refund for order {order.ref}"
    _send(order, subject, lines, _pdf(credit_note))


def invoice_document(order, invoice, replaced=None) -> None:
    """A document issued by the team: a corrected invoice, or a cancellation."""
    if _de(order):
        text = f"im Anhang finden Sie die korrigierte Rechnung {invoice.number}. Sie ersetzt die Rechnung {replaced.number}, die storniert wurde." if replaced else f"im Anhang finden Sie das Dokument {invoice.number} zur Bestellung {order.ref}."
        lines, subject = [f"Guten Tag {_name(order)},", "", text], f"Rechnung {invoice.number}"
    else:
        text = f"Attached is the corrected invoice {invoice.number}. It replaces invoice {replaced.number}, which has been cancelled." if replaced else f"Attached is the document {invoice.number} for order {order.ref}."
        lines, subject = [f"Hello {_name(order)},", "", text], f"Invoice {invoice.number}"
    _send(order, subject, lines, _pdf(invoice))
