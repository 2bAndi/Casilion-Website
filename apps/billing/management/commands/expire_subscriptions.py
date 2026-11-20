from django.core.management.base import BaseCommand

from apps.billing.services import expire_due


class Command(BaseCommand):
    help = "Return accounts whose paid period (plus grace) is over to the Community plan. Run daily."

    def handle(self, *args, **options):
        self.stdout.write(f"{expire_due()} subscription(s) ended.")
