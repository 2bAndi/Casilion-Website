"""What the security review of November 2026 closed. Each test is one door that stays shut."""
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.backoffice.models import SupportSettings
from apps.billing.models import PaymentSettings
from apps.mailing import inbox
from apps.mailing.models import Conversation, MailLog
from apps.mcp_server import operator as operator_tools
from apps.mcp_server import tokens
from apps.mcp_server.auth import Principal
from apps.mcp_server.models import AgentToken

from .base import CassilionTestCase, make_customer


def with_second_factor(user):
    Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": "test"})
    return user


class SecurityTestCase(CassilionTestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner@example.com", "a-long-test-password")
        self.operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)

    def strict(self, **more):
        return self.settings(CASSILION={**settings.CASSILION, "REQUIRE_MFA_FOR_STAFF": True, **more})


class BackofficeDoorTests(SecurityTestCase):
    def test_a_team_login_without_second_factor_does_not_open_the_backoffice(self):
        self.client.force_login(self.operator)
        with self.strict():
            for name in ["dashboard", "customers", "orders", "tickets", "audit_log"]:
                self.assertRedirects(self.client.get(reverse(f"backoffice:{name}")), reverse("mfa_index"), fetch_redirect_response=False)
            with_second_factor(self.operator)
            self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 200)

    def test_an_operator_token_is_worth_nothing_without_the_second_factor_and_ends_after_ninety_days(self):
        self.client.force_login(self.operator)
        self.client.post(reverse("backoffice:operator_token_create"), {"name": "assistant"})
        record = AgentToken.objects.get(kind=AgentToken.Kind.OPERATOR)
        self.assertLessEqual((record.expires_at - record.created_at).days, 90)
        _record, plaintext = tokens.issue(self.operator, kind=AgentToken.Kind.OPERATOR)
        with self.strict():
            self.assertIsNone(tokens.lookup(plaintext, AgentToken.Kind.OPERATOR))
            with_second_factor(self.operator)
            self.assertIsNotNone(tokens.lookup(plaintext, AgentToken.Kind.OPERATOR))

    def test_a_team_login_ends_after_a_working_day(self):
        self.assertTrue(self.client.login(email="ops@example.com", password="a-long-test-password"))
        self.assertLessEqual(self.client.session.get_expiry_age(), 12 * 3600)
        user, _account = make_customer()
        self.client.logout()
        self.assertTrue(self.client.login(email=user.email, password="a-long-test-password"))
        self.assertGreater(self.client.session.get_expiry_age(), 24 * 3600)

    def test_money_keys_and_the_mailbox_are_for_owners(self):
        self.client.force_login(self.operator)
        self.assertNotContains(self.client.get(reverse("backoffice:orders")), "Payment settings")
        for name in ["payment_settings", "inbox_settings"]:
            self.assertRedirects(self.client.get(reverse(f"backoffice:{name}")), reverse("backoffice:dashboard"), fetch_redirect_response=False)
        before = PaymentSettings.load().iban
        self.client.post(reverse("backoffice:payment_settings"), {"section": "bank_transfer", "iban": "DE02120300000000202051", "bank_account_holder": "X", "account_holder": "X"})
        self.assertEqual(PaymentSettings.load().iban, before)
        self.assertEqual(self.client.post(reverse("backoffice:purge_test_orders"), {"confirm": "on"}).status_code, 302)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("backoffice:payment_settings")).status_code, 200)
        self.assertContains(self.client.get(reverse("backoffice:orders")), "Payment settings")


class AssistantTests(SecurityTestCase):
    def test_the_assistant_can_stop_automatic_sending_but_not_start_it(self):
        principal = Principal(user=self.operator, account=None, token=None)
        limits = SupportSettings.load()
        limits.auto_send = False
        limits.save()
        self.assertEqual(operator_tools._set_auto_send(principal, True)["error"], "backoffice_only")
        self.assertFalse(SupportSettings.load().auto_send)
        SupportSettings.objects.update(auto_send=True)
        self.assertTrue(operator_tools._set_auto_send(principal, False)["ok"])
        self.assertFalse(SupportSettings.load().auto_send)


class SandboxTests(SecurityTestCase):
    def test_without_named_testers_the_sandbox_is_for_the_team_only(self):
        user, _account = make_customer()
        config = PaymentSettings.load()
        config.sandbox_enabled, config.sandbox_testers = True, ""
        with self.settings(CASSILION={**settings.CASSILION, "SANDBOX_FOR_EVERYONE": False}):
            self.assertFalse(config.sandbox_for(user))
            self.assertTrue(config.sandbox_for(self.operator))
            config.sandbox_testers = user.email
            self.assertTrue(config.sandbox_for(user))


RAW = (
    "From: Someone Else <stranger@elsewhere.example>\r\nReply-To: {reply}\r\nTo: info@cassilion.com\r\n"
    "Subject: My license\r\nMessage-ID: <{mid}@elsewhere.example>\r\nDate: Fri, 20 Nov 2026 10:00:00 +0100\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n\r\nPlease send me my license file again.\r\n"
)


class MailTests(SecurityTestCase):
    def test_a_reply_address_does_not_make_a_stranger_a_customer(self):
        user, account = make_customer()
        inbox.store(RAW.format(reply=user.email, mid="a1").encode())
        thread = Conversation.objects.get()
        self.assertIsNone(thread.account)
        self.client.force_login(self.operator)
        page = self.client.get(reverse("backoffice:conversation", args=[thread.pk]))
        self.assertContains(page, "asks for the answer at another address")

    @override_settings(ADMINS=[("Operator", "ops@cassilion.example")])
    def test_server_error_reports_are_not_kept_in_the_mail_log(self):
        mail.mail_admins("Internal Server Error: /ops/payments/settings/", "Traceback ... POST: {'stripe_secret_key': 'sk_live_x'}")
        entry = MailLog.objects.latest("pk")
        self.assertNotIn("sk_live_x", entry.body)
        self.assertEqual(len(mail.outbox), 1)  # it is still delivered to the operators

    def test_the_notice_mail_points_to_the_backoffice_page(self):
        self.client.post(reverse("core:withdraw"), {"lang": "de", "name": "Dana Beispiel", "contract": "CO-ABC1234", "email": "dana@example.org"})
        team_mail = next(m for m in mail.outbox if m.to == settings.CASSILION["OPERATOR_EMAILS"])
        self.assertIn("/ops/notices/", team_mail.body)
        self.assertNotIn("/ops/admin/", team_mail.body)
