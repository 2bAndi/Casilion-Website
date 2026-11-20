"""Bank transfer in advance. The money is confirmed from a bank statement or by hand."""
from decimal import Decimal

from django.urls import reverse

from apps.billing import events
from apps.billing.models import Payment, PaymentStatus

from .base import PaymentProvider, RefundResult, StartResult


def instructions(order, config) -> dict:
    return {
        "holder": config.bank_account_holder,
        "iban": config.iban,
        "bic": config.bic,
        "bank": config.bank_name,
        "reference": order.ref,
        "amount": order.total,
        "due": order.due_date,
    }


class BankTransfer(PaymentProvider):
    code = "bank_transfer"
    label = "Bank transfer"

    def start(self, order, request) -> StartResult:
        from . import config

        payment, created = Payment.objects.get_or_create(
            order=order, provider=self.code, status=PaymentStatus.PENDING, defaults={"amount": order.total}
        )
        if created:
            events.created(payment, source="customer")
            if order.kind == "new":  # a renewal already came with its invoice and the bank details
                from apps.billing import mails

                mails.bank_transfer_instructions(order, config())
        return StartResult(redirect_url=reverse("billing:order", args=[order.ref]), instructions=instructions(order, config()))

    def parse_webhook(self, body, headers, config):  # no provider callbacks
        raise NotImplementedError

    def refund(self, payment, amount: Decimal) -> RefundResult:
        # The money goes back by a manual transfer; the backoffice says so when the refund is booked.
        return RefundResult(provider_id="manual-transfer")
