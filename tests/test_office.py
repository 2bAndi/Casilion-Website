"""The backoffice pages that replace the raw-data screens: audit log, releases, team,
a plan by hand, blocking a login, withdrawals and cancellations."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.log import audit
from apps.audit.models import AuditEvent
from apps.billing.models import Plan
from apps.core.models import ContractNotice
from apps.downloads.models import Release
from apps.licensing.services import current_entitlement
from apps.mcp_server import tokens
from apps.mcp_server.models import AgentToken

from .base import CassilionTestCase, make_customer


class OfficeTestCase(CassilionTestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner@example.com", "a-long-test-password", full_name="Olivia Owner")
        self.operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.user, self.account = make_customer(email="dana@example.com", plan="community")
        self.client.force_login(self.operator)


class NavigationTests(OfficeTestCase):
    def test_one_menu_with_areas_and_their_pages(self):
        page = self.client.get(reverse("backoffice:dashboard"))
        for area in ["Overview", "Support", "Customers", "Sales", "Products", "Operations"]:
            self.assertContains(page, area)
        self.assertNotContains(page, "Raw data")
        # Inside an area its pages are listed, and the one you are on is marked.
        sales = self.client.get(reverse("backoffice:invoices"))
        for name in ["orders", "invoices", "payment_issues", "bank_import", "payment_settings"]:
            self.assertContains(sales, f'href="{reverse(f"backoffice:{name}")}"')
        self.assertContains(sales, f'href="{reverse("backoffice:invoices")}" aria-current="page"')
        self.assertNotContains(sales, reverse("backoffice:audit_log"))

    def test_every_page_knows_its_place_in_the_menu(self):
        from apps.backoffice import nav

        places = {
            "/ops/": ("Overview", "Overview"),
            "/ops/review/": ("Support", "Review queue"),
            "/ops/tickets/T-ABC123/": ("Support", "Tickets"),
            "/ops/inbox/5/": ("Support", "Inbox"),
            "/ops/inbox/settings/": ("Support", "Mailbox"),
            "/ops/settings/": ("Support", "Support settings"),
            f"/ops/customers/{self.account.pk}/": ("Customers", "Customers"),
            "/ops/notices/W-1/": ("Customers", "Withdrawals and cancellations"),
            "/ops/orders/CO-ABCDEFG/": ("Sales", "Orders"),
            "/ops/payments/": ("Sales", "Payments"),
            "/ops/payments/bank/": ("Sales", "Bank statement"),
            "/ops/payments/settings/": ("Sales", "Payment settings"),
            "/ops/plans/community/": ("Products", "Products and plans"),
            "/ops/releases/3/": ("Products", "Releases"),
            "/ops/team/": ("Operations", "Team"),
            "/ops/audit/": ("Operations", "Audit log"),
        }
        for path, (area, page) in places.items():
            menu = nav.build(path)
            self.assertEqual(menu["area"].label, area, path)
            self.assertEqual([p.label for a in menu["areas"] for p in a.pages if p.current], [page], path)

    def test_what_waits_is_counted_in_the_menu(self):
        from apps.backoffice import nav

        ContractNotice.objects.create(kind="cancellation", name="Dana", email="dana@example.com", contract="Professional")
        menu = nav.build("/ops/")
        customers = next(a for a in menu["areas"] if a.label == "Customers")
        self.assertEqual((customers.number, [p.number for p in customers.pages]), (1, [0, 1]))
        self.assertContains(self.client.get(reverse("backoffice:customers")), '<span class="count">1</span>')

    def test_customers_get_none_of_it(self):
        self.client.force_login(self.user)
        for name in ["releases", "release_new", "notices", "team", "audit_log"]:
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}")).status_code, 302, name)


class AuditLogTests(OfficeTestCase):
    def test_log_can_be_filtered_and_not_changed(self):
        audit("payment.settings_changed", actor=self.owner, actor_kind="operator", obj="stripe", data={"fields": ["stripe_secret_key"]})
        audit("customer.updated", actor=self.operator, actor_kind="operator", account=self.account, data={"fields": ["account.name"]})
        audit("customer.viewed", actor=self.operator, actor_kind="operator", account=self.account)
        url = reverse("backoffice:audit_log")
        everything = self.client.get(url)
        for expected in ["payment.settings_changed", "customer.updated", "customer.viewed", "stripe_secret_key", "owner@example.com", "Example GmbH"]:
            self.assertContains(everything, expected)
        payments = self.client.get(url, {"area": "payment"})
        self.assertContains(payments, "payment.settings_changed")
        self.assertNotContains(payments, "customer.updated")
        by_owner = self.client.get(url, {"who": "owner@"})
        self.assertNotContains(by_owner, "customer.updated")
        changes = self.client.get(url, {"area": "customer", "changes": "1"})
        self.assertContains(changes, "customer.updated")
        self.assertNotContains(changes, "customer.viewed")
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertContains(self.client.get(url, {"from": tomorrow}), "Nothing matches")
        self.assertContains(self.client.get(url, {"q": "example gmbh", "to": tomorrow}), "customer.updated")
        self.assertEqual(self.client.post(url, {"delete": "1"}).status_code, 200)  # there is nothing to post to
        self.assertEqual(AuditEvent.objects.filter(action="customer.updated").count(), 1)


class ReleaseTests(OfficeTestCase):
    def test_release_with_a_file_from_the_server_is_published_to_customers(self):
        self.client.post(reverse("backoffice:release_new"), {"action": "save", "version": "1.2.0", "channel": "stable", "notes": "Faster compaction."})
        release = Release.objects.get()
        self.assertEqual((release.version, release.is_published, release.published_at), ("1.2.0", False, None))
        url = reverse("backoffice:release", args=[release.pk])
        self.assertContains(self.client.get(url), "No new file found")

        folder = Path(settings.CASSILION["RELEASES_DIR"]) / "1.2.0"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cassilion-1.2.0-linux-x86_64.tar.gz").write_bytes(b"build")
        (folder.parent / "secret.txt").write_text("not part of any release")
        self.assertContains(self.client.get(url), "1.2.0/cassilion-1.2.0-linux-x86_64.tar.gz")
        # Only files of this release's own folder can be offered.
        self.client.post(url, {"action": "add_file", "file": "secret.txt", "platform": "linux-x86_64"})
        self.client.post(url, {"action": "add_file", "file": "../../etc/passwd", "platform": "linux-x86_64"})
        self.assertEqual(release.artefacts.count(), 0)
        self.client.post(url, {"action": "add_file", "file": "1.2.0/cassilion-1.2.0-linux-x86_64.tar.gz", "platform": "linux-x86_64"})
        artefact = release.artefacts.get()
        self.assertEqual((artefact.size_bytes, len(artefact.sha256)), (5, 64))

        self.client.post(url, {"action": "save", "version": "1.2.0", "channel": "stable", "notes": "Faster compaction.", "is_published": "on"})
        release.refresh_from_db()
        self.assertTrue(release.is_published and release.published_at)
        self.assertContains(self.client.get(reverse("backoffice:releases")), "Published")
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("downloads:downloads")), "1.2.0")
        self.assertEqual(sorted(AuditEvent.objects.filter(action__startswith="release.").values_list("action", flat=True)), ["release.file_added", "release.saved", "release.saved"])

        # A file somebody downloaded stays on record; one nobody fetched can go.
        self.client.force_login(self.operator)
        from apps.downloads.models import Download

        Download.objects.create(account=self.account, user=self.user, artefact=artefact)
        self.client.post(url, {"action": "remove_file", "artefact": artefact.pk})
        self.assertEqual(release.artefacts.count(), 1)
        Download.objects.all().delete()
        self.client.post(url, {"action": "remove_file", "artefact": artefact.pk})
        self.assertEqual(release.artefacts.count(), 0)
        self.assertTrue((folder / "cassilion-1.2.0-linux-x86_64.tar.gz").exists())


class PlanByHandTests(OfficeTestCase):
    def test_a_plan_is_given_and_taken_back_by_hand(self):
        enterprise = Plan.objects.get(code="enterprise")
        until = timezone.localdate() + timedelta(days=90)
        url = reverse("backoffice:grant_plan", args=[self.account.pk])
        self.client.post(url, {"plan": enterprise.pk, "valid_until": until.isoformat(), "note": ""})  # no reason, no plan
        self.assertEqual(current_entitlement(self.account).plan.code, "community")
        self.client.post(url, {"plan": enterprise.pk, "valid_until": until.isoformat(), "note": "Contract of 20 Nov, signed"})
        entitlement = current_entitlement(self.account)
        self.assertEqual((entitlement.plan.code, entitlement.source, entitlement.valid_until), ("enterprise", "manual", until))
        self.assertEqual(entitlement.licenses.filter(revoked_at__isnull=True).count(), 1)
        line = AuditEvent.objects.get(action="license.granted_by_hand")
        self.assertEqual((line.actor, line.account, line.data["note"]), (self.operator, self.account, "Contract of 20 Nov, signed"))
        page = self.client.get(reverse("backoffice:customer", args=[self.account.pk]), {"tab": "licenses"})
        self.assertContains(page, "Granted by the team")
        self.assertContains(page, "End it")
        self.client.post(reverse("backoffice:end_plan", args=[self.account.pk]), {"entitlement": entitlement.pk})
        self.assertEqual(current_entitlement(self.account).plan.code, "community")

    def test_not_on_top_of_a_plan_the_customer_pays_for(self):
        _user, paying = make_customer(email="paul@example.com", plan="professional", name="Paying AG")
        self.client.post(reverse("backoffice:grant_plan", args=[paying.pk]), {"plan": Plan.objects.get(code="business").pk, "note": "Goodwill"})
        self.assertEqual(current_entitlement(paying).plan.code, "professional")
        # A plan of another product is fine.
        self.client.post(reverse("backoffice:grant_plan", args=[paying.pk]), {"plan": Plan.objects.get(code="vakaluja-community").pk, "note": "Partner"})
        self.assertEqual(current_entitlement(paying, Plan.objects.get(code="vakaluja-community").product).source, "manual")


class LoginTests(OfficeTestCase):
    def test_a_blocked_login_cannot_sign_in_and_its_agent_token_stops(self):
        _record, token = tokens.issue(self.user, kind=AgentToken.Kind.CUSTOMER, name="laptop", account=self.account)
        self.assertIsNotNone(tokens.lookup(token, AgentToken.Kind.CUSTOMER))
        url = reverse("backoffice:login_block", args=[self.account.pk])
        self.client.post(url, {"user": self.user.pk, "action": "block"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNone(tokens.lookup(token, AgentToken.Kind.CUSTOMER))
        self.assertFalse(self.client.login(email="dana@example.com", password="a-long-test-password"))
        self.assertContains(self.client.get(reverse("backoffice:customer", args=[self.account.pk])), "Blocked")
        self.client.post(url, {"user": self.user.pk, "action": "unblock"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        # Only logins of this customer, and never a team member, through this page.
        self.client.post(url, {"user": self.owner.pk, "action": "block"})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertEqual(list(AuditEvent.objects.filter(action__startswith="customer.login").values_list("action", flat=True)), ["customer.login_unblocked", "customer.login_blocked"])


class TeamTests(OfficeTestCase):
    def test_only_owners_decide_who_is_in_the_team(self):
        url, change = reverse("backoffice:team"), reverse("backoffice:team_change")
        page = self.client.get(url)  # an operator sees the team
        self.assertContains(page, "owner@example.com")
        self.assertContains(page, "Only an owner can")
        self.client.post(change, {"action": "add", "email": "dana@example.com"})  # but cannot change it
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)

        self.client.force_login(self.owner)
        self.client.post(change, {"action": "add", "email": "nobody@example.com"})
        self.client.post(change, {"action": "add", "email": "Dana@example.com"})
        self.user.refresh_from_db()
        self.assertEqual((self.user.is_staff, self.user.is_superuser), (True, False))
        self.client.post(change, {"action": "make_owner", "user": self.user.pk})
        self.client.post(change, {"action": "block", "user": self.operator.pk})
        self.user.refresh_from_db()
        self.operator.refresh_from_db()
        self.assertEqual((self.user.is_superuser, self.operator.is_active), (True, False))
        self.client.post(change, {"action": "remove", "user": self.user.pk})
        self.user.refresh_from_db()
        self.assertEqual((self.user.is_staff, self.user.is_superuser, self.user.is_active), (False, False, True))
        # Nobody locks themselves out.
        for action in ("remove", "block", "unmake_owner"):
            self.client.post(change, {"action": action, "user": self.owner.pk})
        self.owner.refresh_from_db()
        self.assertEqual((self.owner.is_staff, self.owner.is_superuser, self.owner.is_active), (True, True, True))
        self.assertEqual(AuditEvent.objects.filter(action__startswith="team.", actor=self.owner).count(), 4)


class NoticeTests(OfficeTestCase):
    def test_a_withdrawal_is_seen_and_handled_without_changing_what_was_declared(self):
        notice = ContractNotice.objects.create(kind="withdrawal", name="Dana Demo", email="dana@example.com", contract="Order CO-ABCDEFG of 20 November")
        self.assertContains(self.client.get(reverse("backoffice:dashboard")), "to be handled")
        self.assertContains(self.client.get(reverse("backoffice:notices")), notice.reference)
        self.assertContains(self.client.get(reverse("backoffice:customer", args=[self.account.pk])), notice.reference)
        url = reverse("backoffice:notice", args=[notice.reference])
        page = self.client.get(url)
        self.assertContains(page, "Order CO-ABCDEFG of 20 November")
        self.assertContains(page, "Example GmbH")
        self.client.post(url, {"action": "done", "note": "Refunded in full on the order.", "contract": "something else", "email": "eve@example.com"})
        notice.refresh_from_db()
        self.assertEqual((notice.contract, notice.email, notice.handling_note), ("Order CO-ABCDEFG of 20 November", "dana@example.com", "Refunded in full on the order."))
        self.assertIsNotNone(notice.handled_at)
        self.assertNotContains(self.client.get(reverse("backoffice:notices")), notice.reference)
        self.assertContains(self.client.get(reverse("backoffice:notices") + "?show=done"), notice.reference)
        self.assertNotContains(self.client.get(reverse("backoffice:dashboard")), "to be handled")
        self.assertTrue(AuditEvent.objects.filter(action="notice.handled", actor=self.operator, object_ref=notice.reference).exists())
