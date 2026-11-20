"""Every page renders for the audience it is meant for, and stays closed to everyone else."""
from django.urls import reverse

from apps.accounts.models import User
from apps.downloads.models import Download, Release
from apps.downloads.services import register_artefact
from apps.mcp_server.models import AgentToken
from apps.support import services as support
from apps.support.models import Message, Ticket

from .base import CassilionTestCase, make_customer, run_worker_once

PUBLIC = ["core:home", "core:docs", "billing:pricing", "account_login", "account_signup", "licensing:public_key", "core:healthz"]
CUSTOMER = ["accounts:dashboard", "accounts:settings", "billing:overview", "licensing:licenses", "downloads:downloads", "support:list", "support:new", "mcp_server:agents"]
OPERATOR = ["backoffice:dashboard", "backoffice:review_queue", "backoffice:tickets", "backoffice:sent_log", "backoffice:settings"]


class PageTests(CassilionTestCase):
    def setUp(self):
        self.user, self.account = make_customer()
        self.operator = User.objects.create_user("ops@cassilion.example", "a-long-test-password", is_staff=True, is_superuser=True)

    def test_public_pages(self):
        for name in PUBLIC:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        home = self.client.get(reverse("core:home"))
        self.assertContains(home, "js/landing.")  # the landing page with its charts (the file name is hashed in production)
        self.assertContains(home, "video/node-globe.")  # the rotating globe behind the hero
        self.assertContains(home, "<span>Cassilion</span>", count=2)  # the logo, in header and footer
        self.assertNotContains(home, "#}")  # no template comment leaks onto the page
        self.assertContains(home, "Priority handling by the Cassilion team.")  # plans come from the database
        self.assertNotContains(home, ' style="')  # the content security policy forbids inline styles
        self.assertEqual(self.client.get(reverse("core:doc", args=["configuration"])).status_code, 200)
        self.assertContains(self.client.get(reverse("core:docs"), {"q": "memory cache"}), "cache.max_memory")
        self.assertEqual(self.client.get(reverse("core:legal", args=["privacy"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:doc", args=["nope"])).status_code, 404)

    def test_search_engines_are_kept_out_until_switched_on(self):
        from django.test import override_settings

        with override_settings(SITE_NOINDEX=True):
            home = self.client.get(reverse("core:home"))
            self.assertEqual(home["X-Robots-Tag"], "noindex, nofollow")
            self.assertContains(home, '<meta name="robots" content="noindex, nofollow">')
            self.assertEqual(self.client.get("/robots.txt").content.decode(), "User-agent: *\nDisallow: /\n")
        with override_settings(SITE_NOINDEX=False):
            home = self.client.get(reverse("core:home"))
            self.assertNotIn("X-Robots-Tag", home)
            self.assertNotContains(home, 'name="robots"')
            robots = self.client.get("/robots.txt").content.decode()
            self.assertIn("Disallow: /account/", robots)
            self.assertNotIn("Disallow: /\n", robots)

    def test_health_check_answers_under_any_host_name(self):
        response = self.client.get("/healthz", headers={"Host": "127.0.0.1:8000"})
        self.assertEqual((response.status_code, response.json()["ok"]), (200, True))

    def test_customer_area_needs_a_login(self):
        for name in CUSTOMER + OPERATOR:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)

    def test_customer_pages(self):
        self.client.force_login(self.user)
        for name in CUSTOMER:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for name in OPERATOR:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)  # customers are not let into the backoffice

    def test_ticket_through_the_customer_area_and_the_backoffice(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("support:new"), {
            "subject": "How do I limit the memory Cassilion uses?",
            "product_version": "0.9.0",
            "body": "We run Cassilion on a small VM. How can I limit the memory it uses for caching?",
        })
        ticket = Ticket.objects.get()
        self.assertRedirects(response, reverse("support:detail", args=[ticket.public_id]))
        run_worker_once()
        page = self.client.get(reverse("support:detail", args=[ticket.public_id]))
        self.assertContains(page, "With the Cassilion team")
        self.assertNotContains(page, "cache.max_memory")  # the draft is not visible to the customer

        self.client.force_login(self.operator)
        for name in OPERATOR:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        ops_page = self.client.get(reverse("backoffice:ticket", args=[ticket.public_id]))
        self.assertContains(ops_page, "cache.max_memory")
        self.assertContains(ops_page, "Guard report")
        draft = ticket.messages.get(state=Message.State.DRAFT)
        self.client.post(reverse("backoffice:ticket", args=[ticket.public_id]), {"action": "approve", "draft": draft.pk, "body": draft.body + "\nGood luck!"})
        self.client.post(reverse("backoffice:ticket", args=[ticket.public_id]), {"action": "reply", "body": "Internal: check the docs wording.", "internal": "on"})
        self.assertContains(self.client.get(reverse("backoffice:sent_log")), "Edited and approved")

        self.client.force_login(self.user)
        page = self.client.get(reverse("support:detail", args=[ticket.public_id]))
        self.assertContains(page, "Good luck!")
        self.assertContains(page, "Written by AI")
        self.assertNotContains(page, "check the docs wording")
        self.client.post(reverse("support:detail", args=[ticket.public_id]), {"body": "Thanks, but it still uses too much memory."})
        self.client.post(reverse("support:human", args=[ticket.public_id]))
        ticket.refresh_from_db()
        self.assertTrue(ticket.needs_human)

    def test_customers_only_see_their_own_tickets(self):
        other_user, other_account = make_customer(email="other@example.com", name="Other AG")
        foreign = support.open_ticket(other_account, other_user, channel="web", subject="Private", body="This belongs to another customer.")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("support:detail", args=[foreign.public_id])).status_code, 404)

    def test_download_with_expiring_link(self):
        from django.conf import settings
        from pathlib import Path

        build = Path(settings.CASSILION["RELEASES_DIR"]) / "0.9.0" / "cassilion-0.9.0-linux-x86_64.tar.gz"
        build.parent.mkdir(parents=True, exist_ok=True)
        build.write_bytes(b"build")
        release = Release.objects.create(version="0.9.0", is_published=True)
        artefact = register_artefact(release, "linux-x86_64", build)

        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("downloads:downloads")), "cassilion-0.9.0-linux-x86_64.tar.gz")
        link = self.client.post(reverse("downloads:request_link", args=[artefact.pk]))
        self.client.logout()  # the link is the credential: an agent can fetch it without a session
        response = self.client.get(link.url)
        self.assertEqual(b"".join(response.streaming_content), b"build")
        self.assertEqual(Download.objects.get().account, self.account)
        self.assertEqual(self.client.get(reverse("downloads:file", args=["forged-token"])).status_code, 404)

    def test_agent_token_is_shown_once(self):
        from django.conf import settings
        from django.test import override_settings

        self.client.force_login(self.user)
        with override_settings(CASSILION={**settings.CASSILION, "REQUIRE_MFA_FOR_AGENT_TOKENS": False}):
            self.client.post(reverse("mcp_server:create"), {"name": "laptop"})
        first = self.client.get(reverse("mcp_server:agents"))
        self.assertContains(first, "cas_agent_")
        token = AgentToken.objects.get()
        self.assertNotContains(self.client.get(reverse("mcp_server:agents")), first.context["new_token"])
        self.client.post(reverse("mcp_server:revoke", args=[token.pk]))
        token.refresh_from_db()
        self.assertIsNotNone(token.revoked_at)

    def test_agent_token_can_require_two_factor(self):
        from django.conf import settings
        from django.test import override_settings

        self.client.force_login(self.user)
        with override_settings(CASSILION={**settings.CASSILION, "REQUIRE_MFA_FOR_AGENT_TOKENS": True}):
            self.client.post(reverse("mcp_server:create"), {"name": "laptop"})
            self.assertContains(self.client.get(reverse("mcp_server:agents")), "two-factor authentication")
        self.assertFalse(AgentToken.objects.exists())


class OperatorCommandTests(CassilionTestCase):
    """create_operator: a staff account that can sign in on a site with mandatory address confirmation."""

    def test_operator_can_sign_in_at_once(self):
        import os
        from unittest import mock

        from allauth.account.models import EmailAddress
        from django.core.management import call_command
        from django.test import override_settings

        with mock.patch.dict(os.environ, {"OPERATOR_PASSWORD": "a-long-operator-password"}):
            call_command("create_operator", "Ops@Cassilion.Example", name="Olivia Operator", verbosity=0)
        user = User.objects.get(email="ops@cassilion.example")
        self.assertTrue(user.is_staff and user.is_superuser)
        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        with override_settings(ACCOUNT_EMAIL_VERIFICATION="mandatory"):
            response = self.client.post(reverse("account_login"), {"login": "ops@cassilion.example", "password": "a-long-operator-password"})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 200)

        customer, _account = make_customer(email="later-operator@example.com")
        call_command("create_operator", "later-operator@example.com", verbosity=0)
        customer.refresh_from_db()
        self.assertTrue(customer.is_staff)
