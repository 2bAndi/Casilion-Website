from django.core.management.base import BaseCommand

from apps.billing.services import expire_due, renew_due


class Command(BaseCommand):
    help = (
        "Daily: send the invoice for the next term to plans that end soon, remind about unpaid ones, "
        "and return accounts whose paid period (plus grace) is over to the free plan."
    )

    def handle(self, *args, **options):
        self.stdout.write(f"{renew_due()} renewal invoice(s) sent.")
        self.stdout.write(f"{expire_due()} subscription(s) ended.")
