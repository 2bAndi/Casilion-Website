from django.core.management.base import BaseCommand

from apps.billing.payments import reconcile


class Command(BaseCommand):
    help = "Nightly: compare our payments with what Stripe and PayPal list themselves. Differences go to the backoffice."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=3)

    def handle(self, *args, **options):
        self.stdout.write(f"{reconcile(options['days'])} new difference(s).")
