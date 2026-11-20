from django.core.management.base import BaseCommand

from apps.support.services import close_stale


class Command(BaseCommand):
    help = "Close answered tickets and tickets waiting for the customer after the quiet period. Run daily."

    def handle(self, *args, **options):
        self.stdout.write(f"{close_stale()} ticket(s) closed.")
