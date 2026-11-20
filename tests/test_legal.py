"""Legal pages and the functions German consumer law asks for: withdrawal, cancellation, binding order."""
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import Account
from apps.billing.models import Order
from apps.core import legal
from apps.core.models import ContractNotice

from .base import ADDRESS, CassilionTestCase, make_customer


class LegalTextTests(CassilionTestCase):
    def test_every_text_exists_in_both_languages(self):
        for slug in legal.LEGAL_PAGES:
            for lang in legal.LANGUAGES:
                response = self.client.get(reverse("core:legal", args=[slug]), {"lang": lang})
                self.assertEqual(response.status_code, 200, (slug, lang))
                self.assertContains(response, "Zuri-AI GmbH")
                self.assertNotContains(response, "{{")
                self.assertNotContains(response, ".example")  # no placeholder address left in a legal text
        self.assertEqual(self.client.get(reverse("core:legal", args=["nope"])).status_code, 404)

    def test_imprint_carries_the_mandatory_details(self):
        page = self.client.get(reverse("core:legal", args=["imprint"]), {"lang": "de"})
        for expected in ["Lautzenhardtweg 10", "63069 Offenbach am Main", "Geschäftsführer", "HRB 57727", "Amtsgericht Offenbach am Main", "mailto:", "tel:+4915111572555"]:
            self.assertContains(page, expected)
        self.assertNotContains(page, "Umsatzsteuer-Identifikationsnummer")  # only shown once one is configured
        self.assertNotContains(page, "ec.europa.eu")  # the EU dispute platform was closed in July 2025
        company = dict(self._company(), VAT_ID="DE123456789")
        with override_settings(COMPANY=company):
            self.assertContains(self.client.get(reverse("core:legal", args=["imprint"]), {"lang": "de"}), "DE123456789")

    def _company(self):
        from django.conf import settings

        return settings.COMPANY

    def test_withdrawal_policy_names_the_online_function(self):
        page = self.client.get(reverse("core:legal", args=["withdrawal"]), {"lang": "de"})
        self.assertContains(page, "Sie können Ihr Widerrufsrecht auch online unter")
        self.assertContains(page, reverse("core:withdraw"))
        self.assertContains(page, "Muster-Widerrufsformular")

    def test_language_follows_the_request(self):
        self.assertContains(self.client.get(reverse("core:legal", args=["terms"])), "Terms and conditions")
        self.assertContains(self.client.get(reverse("core:legal", args=["terms"]), headers={"Accept-Language": "de"}), "Allgemeine Geschäftsbedingungen")

    def test_every_page_links_the_texts_and_both_functions(self):
        for name in ["core:home", "billing:pricing", "account_login"]:
            page = self.client.get(reverse(name))
            for target in ["imprint", "privacy", "terms", "withdrawal"]:
                self.assertContains(page, reverse("core:legal", args=[target]), msg_prefix=name)
            self.assertContains(page, "(Impressum)", msg_prefix=name)
            self.assertContains(page, "Vertrag widerrufen", msg_prefix=name)
            self.assertContains(page, "Verträge hier kündigen", msg_prefix=name)


class WithdrawalFunctionTests(CassilionTestCase):
    def test_withdrawal_needs_no_login_and_is_confirmed_at_once(self):
        form = self.client.get(reverse("core:withdraw"), {"lang": "de"})
        self.assertContains(form, "Widerruf bestätigen")
        self.assertNotContains(form, "cancel_reason")  # the law forbids asking for a reason
        self.assertNotContains(form, "<textarea")

        response = self.client.post(reverse("core:withdraw"), {"lang": "de", "name": "Dana Beispiel", "contract": "ord_abc123, Supporter", "email": "dana@example.org"})
        notice = ContractNotice.objects.get()
        self.assertRedirects(response, reverse("core:notice_receipt", args=[notice.token]) + "?lang=de")
        self.assertEqual((notice.kind, notice.contract, notice.confirmation_sent), ("withdrawal", "ord_abc123, Supporter", True))

        receipt, to_team = mail.outbox
        self.assertEqual(receipt.to, ["dana@example.org"])
        for expected in ["Hiermit widerrufe ich", "ord_abc123, Supporter", "Eingegangen am", notice.reference]:
            self.assertIn(expected, receipt.body)
        self.assertNotIn("wirksam", receipt.body)  # a receipt, not a statement that the withdrawal is valid
        self.assertEqual(to_team.reply_to, ["dana@example.org"])
        self.assertContains(self.client.get(response.url), "Ihr Widerruf ist eingegangen")

    def test_cancellation_asks_for_kind_and_date(self):
        form = self.client.get(reverse("core:cancel_contract"), {"lang": "de"})
        self.assertContains(form, "Jetzt kündigen")
        data = {"name": "Dana Example", "contract": "Professional, Example GmbH", "email": "dana@example.org", "cancel_type": "extraordinary", "cancel_reason": "Support unreachable for weeks.", "cancel_at": ""}
        response = self.client.post(reverse("core:cancel_contract"), data)
        notice = ContractNotice.objects.get()
        self.assertEqual((notice.kind, notice.cancel_type, notice.language), ("cancellation", "extraordinary", "en"))
        self.assertIn("at the earliest possible date", mail.outbox[0].body)
        self.assertContains(self.client.get(response.url), "We have received your cancellation")

    def test_missing_fields_and_robots_store_nothing(self):
        response = self.client.post(reverse("core:withdraw"), {"name": "", "contract": "x", "email": "not-an-address"})
        self.assertEqual(response.status_code, 200)
        self.client.post(reverse("core:withdraw"), {"name": "Bot", "contract": "x", "email": "bot@example.org", "website": "http://spam"})
        self.assertEqual(ContractNotice.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_form_cannot_be_used_to_flood_an_address(self):
        data = {"name": "Dana", "contract": "ord_1", "email": "dana@example.org"}
        for _ in range(legal.MAX_PER_ADDRESS_PER_HOUR):
            self.client.post(reverse("core:withdraw"), data)
        blocked = self.client.post(reverse("core:withdraw"), data)
        self.assertContains(blocked, "contact@zuri-ai.com")  # the customer is told how to declare it anyway
        self.assertEqual(ContractNotice.objects.count(), legal.MAX_PER_ADDRESS_PER_HOUR)

    def test_receipt_is_only_reachable_with_its_token(self):
        self.assertEqual(self.client.get(reverse("core:notice_receipt", args=["guess"])).status_code, 404)


class OrderPageTests(CassilionTestCase):
    def test_consumer_sees_price_term_and_must_ask_for_early_start(self):
        user, account = make_customer(plan="community", kind=Account.Kind.INDIVIDUAL, name="Dana Example")
        self.client.force_login(user)
        url = reverse("billing:checkout_start", args=["supporter"])
        page = self.client.get(url)
        for expected in ["Order with obligation to pay", "including VAT", "Twelve months", "one month", "before the 14-day withdrawal period has ended"]:
            self.assertContains(page, expected)
        self.assertEqual(Order.objects.count(), 0)  # looking at the order page orders nothing

        self.assertContains(self.client.post(url, ADDRESS), "Please confirm that support should start right away")
        self.assertEqual(Order.objects.count(), 0)

        response = self.client.post(url, {**ADDRESS, "early_start": "on"})
        order = Order.objects.get()
        self.assertIsNotNone(order.early_start_requested_at)
        self.assertTrue(order.terms_version)

        self.client.post(response.url, {"outcome": "pay"})
        confirmation = mail.outbox[-1]
        self.assertIn(order.ref, confirmation.body)
        self.assertIn("expressly asked us to start support", confirmation.body)
        names = [name for name, _content, _type in confirmation.attachments]
        invoice = order.invoices.get()
        self.assertEqual(names, [f"{invoice.number}.pdf", "Cassilion-Terms.html", "Cassilion-Right-of-withdrawal.html"])
        self.assertIn("Model withdrawal form", confirmation.attachments[2][1])
        self.assertIn("/legal/withdraw/", confirmation.attachments[2][1])

    def test_company_is_not_asked_about_withdrawal(self):
        user, _account = make_customer(plan="community")
        self.client.force_login(user)
        url = reverse("billing:checkout_start", args=["professional"])
        self.assertNotContains(self.client.get(url), "early_start")
        self.client.post(url, ADDRESS)
        self.assertIsNone(Order.objects.get().early_start_requested_at)
