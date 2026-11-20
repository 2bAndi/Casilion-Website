from django.core.management.base import BaseCommand

from apps.mailing import inbox
from apps.mailing.models import MailboxSettings


class Command(BaseCommand):
    help = "Read new mails from the company mailbox into the backoffice inbox. Run every few minutes. Changes nothing on the mail server."

    def handle(self, *args, **options):
        config = MailboxSettings.load()
        if not config.enabled:
            self.stdout.write("Reading the mailbox is switched off (Backoffice, Inbox, Settings).")
            return
        added = inbox.fetch(config)
        self.stdout.write(f"{added} new mail(s)." + (f" Problem: {config.last_error}" if config.last_error else ""))
