"""Demo data for local development: plans exist already (migration), this adds people, a release and tokens."""
from pathlib import Path

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Account, User
from apps.accounts.services import create_account
from apps.backoffice.models import SupportSettings
from apps.billing import services as billing
from apps.billing.models import Plan
from apps.billing.providers import get_provider
from apps.billing.providers.base import EVENT_PAID
from apps.downloads.models import Release
from apps.downloads.services import register_artefact
from apps.mcp_server import tokens
from apps.mcp_server.models import AgentToken
from apps.support import services as support
from apps.support.models import Ticket

PASSWORD = "cassilion-dev"


class Command(BaseCommand):
    help = "Create demo accounts, a sample release and agent tokens. Refuses to run outside DEBUG."

    def add_arguments(self, parser):
        parser.add_argument("--tickets", action="store_true", help="Also open two sample tickets.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo creates accounts with a known password. It only runs with DEBUG on.")
        if User.objects.filter(email="demo@example.com").exists():
            self.stdout.write("Demo data is already there.")
            return

        operator = User.objects.create_superuser("ops@cassilion.example", PASSWORD, full_name="Olivia Operator")
        customer = User.objects.create_user("demo@example.com", PASSWORD, full_name="Dana Demo")
        company = create_account(customer, kind=Account.Kind.COMPANY, name="Example GmbH", country="DE")
        free_user = User.objects.create_user("community@example.com", PASSWORD, full_name="Chris Community")
        create_account(free_user, kind=Account.Kind.INDIVIDUAL, name="Chris Community", country="FI")

        for user in (operator, customer, free_user):
            EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

        # Put the company on the Professional plan the same way a purchase would: order, signed provider event.
        order, _url = billing.start_checkout(company, customer, Plan.objects.get(code="professional"))
        body, headers = get_provider("sandbox").build_event(order, EVENT_PAID)
        billing.handle_webhook("sandbox", body, headers)

        releases_dir = Path(settings.CASSILION["RELEASES_DIR"])
        build = releases_dir / "0.9.0" / "cassilion-0.9.0-linux-x86_64.tar.gz"
        build.parent.mkdir(parents=True, exist_ok=True)
        build.write_bytes(b"Placeholder for a Cassilion build. CI publishes the real artefact here.\n")
        release = Release.objects.create(
            version="0.9.0",
            notes="Sample release for the skeleton. Replace with real release notes.",
            is_published=True,
            published_at=timezone.now(),
        )
        register_artefact(release, "linux-x86_64", build)
        windows_build = build.with_name("cassilion-0.9.0-windows-x86_64.zip")
        windows_build.write_bytes(b"Placeholder for a Cassilion build. CI publishes the real artefact here.\n")
        register_artefact(release, "windows-x86_64", windows_build)

        SupportSettings.load()
        _record, customer_token = tokens.issue(customer, kind=AgentToken.Kind.CUSTOMER, account=company, name="demo agent")
        _record, operator_token = tokens.issue(operator, kind=AgentToken.Kind.OPERATOR, name="demo operator agent")

        if options["tickets"]:
            support.open_ticket(
                company, customer, channel=Ticket.Channel.WEB,
                subject="How do I limit the memory Cassilion uses?",
                body="We run Cassilion 0.9.0 on a small VM. How can I limit the memory it uses for caching?",
                product_version="0.9.0",
            )
            support.open_ticket(
                company, customer, channel=Ticket.Channel.MCP,
                subject="Node crashes with a segmentation fault during compaction",
                body="After upgrading to 0.9.0 one node crashes with a segmentation fault whenever compaction starts.",
                product_version="0.9.0",
                diagnostics='{"version": "0.9.0", "signal": "SIGSEGV", "phase": "compaction"}',
            )

        site = settings.CASSILION["SITE_URL"]
        note = (
            "Cassilion demo data (local development only)\n"
            f"  Customer area   {site}/auth/login/   demo@example.com / {PASSWORD}   (Example GmbH, Professional)\n"
            f"  Free customer   community@example.com / {PASSWORD}   (Community)\n"
            f"  Backoffice      {site}/ops/   ops@cassilion.example / {PASSWORD}\n"
            f"  Customer MCP    {site}/mcp       token: {customer_token}\n"
            f"  Operator MCP    {site}/ops/mcp   token: {operator_token}\n"
        )
        (Path(settings.VAR_DIR) / "dev-credentials.txt").write_text(note, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(note))
        self.stdout.write(f"Saved to {Path(settings.VAR_DIR) / 'dev-credentials.txt'} (not in version control).")
