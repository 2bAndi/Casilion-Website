"""Real payment methods (Stripe, PayPal, bank transfer) against recorded-style provider answers,
invoices, refunds, renewals and the products. No request leaves the machine: the providers'
HTTP client is replaced by a transport that plays the provider."""
import hashlib
import hmac
import io
import json
import time
import zipfile
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.billing import payments, services
from apps.billing.models import Order, Payment, PaymentEvent, PaymentSettings, Plan, Product, ReconciliationIssue, Subscription
from apps.billing.providers import http as provider_http
from apps.billing.providers import stripe, usable_methods
from apps.invoicing import services as invoicing
from apps.invoicing.models import ImmutableError, Invoice
from apps.licensing.services import current_entitlement, entitlements

from .base import ADDRESS, CassilionTestCase, make_customer

WHSEC = "whsec_test"


class Provider:
    """Plays Stripe and PayPal: answers by path, remembers what it was asked."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def on(self, method, path, body, status=200):
        self.answers[(method, path)] = (status, body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        content = request.content.decode()
        form = {k: v[0] for k, v in parse_qs(content).items()} if "x-www-form-urlencoded" in request.headers.get("content-type", "") else content
        self.calls.append((request.method, request.url.path, form, dict(request.headers)))
        status, body = self.answers.get((request.method, request.url.path), (404, {"error": {"message": "not played"}}))
        return httpx.Response(status, json=body(request) if callable(body) else body)

    def called(self, method, path):
        return [c for c in self.calls if c[0] == method and c[1] == path]


class PaymentTestCase(CassilionTestCase):
    def setUp(self):
        self.provider = Provider()
        provider_http.TRANSPORT = httpx.MockTransport(self.provider)
        self.addCleanup(setattr, provider_http, "TRANSPORT", None)
        self.config = PaymentSettings.load()
        self.config.stripe_secret_key, self.config.stripe_webhook_secret = "sk_test_abc", WHSEC
        self.config.paypal_client_id, self.config.paypal_secret, self.config.paypal_webhook_id = "pp-client", "pp-secret", "WH-1"
        self.config.bank_account_holder, self.config.iban, self.config.bic = "Zuri-AI GmbH", "DE02 1203 0000 0000 2020 51", "BYLADEM1001"
        self.config.tax_number = "035 123 45678"
        self.config.save()
        self.user, self.account = make_customer(plan="community")
        self.client.force_login(self.user)

    def order(self, plan="professional", method="stripe") -> Order:
        if method == "stripe":
            self.provider.on("POST", "/v1/checkout/sessions", {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"})
        order, self.url = services.start_checkout(self.account, self.user, Plan.objects.get(code=plan), self.request(), method=method)
        return order

    def request(self):
        from django.test import RequestFactory

        return RequestFactory().get("/", secure=True, headers={"host": "testserver"})

    def stripe_event(self, event_type, obj, event_id="evt_1"):
        body = json.dumps({"id": event_id, "type": event_type, "created": int(time.time()), "data": {"object": obj}}).encode()
        stamp = int(time.time())
        signature = hmac.new(WHSEC.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        return self.client.post(
            reverse("billing:webhook", args=["stripe"]), body, content_type="application/json", headers={"Stripe-Signature": f"t={stamp},v1={signature}"}
        )

    def session(self, order, **extra):
        return {"id": "cs_test_1", "status": "complete", "payment_status": "paid", "payment_intent": "pi_1", "client_reference_id": order.ref,
                "amount_total": int(order.total * 100), **extra}


class MethodTests(PaymentTestCase):
    def test_only_configured_methods_are_offered(self):
        self.assertEqual([m.code for m in usable_methods()], ["stripe", "paypal", "bank_transfer", "sandbox"])
        self.config.stripe_secret_key, self.config.methods = "", "paypal,stripe"
        self.config.save()
        self.assertEqual([m.code for m in usable_methods()], ["paypal", "sandbox"])
        self.config.sandbox_enabled = False
        self.config.save()
        self.assertEqual([m.code for m in usable_methods()], ["paypal"])
        # With the sandbox off, its webhook and checkout page do not exist.
        self.assertEqual(self.client.post(reverse("billing:webhook", args=["sandbox"]), b"{}", content_type="application/json").status_code, 400)

    def test_keys_are_stored_encrypted_and_never_shown(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT stripe_secret_key, paypal_secret FROM billing_paymentsettings")
            stored = cursor.fetchone()
        self.assertTrue(all(value.startswith("enc:") for value in stored))
        operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.client.force_login(operator)
        url = reverse("backoffice:payment_settings")
        page = self.client.get(url)
        self.assertNotContains(page, "sk_test_abc")
        self.assertNotContains(page, "pp-secret")
        # An empty key field keeps what is stored; the audit line names fields, not values.
        response = self.client.post(url, {"section": "stripe", "stripe-offer": "on", "stripe-stripe_webhook_secret": "whsec_new"})
        self.assertRedirects(response, url + "#stripe", fetch_redirect_response=False)
        config = PaymentSettings.load()
        self.assertEqual((config.stripe_secret_key, config.stripe_webhook_secret), ("sk_test_abc", "whsec_new"))
        line = AuditEvent.objects.filter(action="payment.settings_changed").get()
        self.assertEqual((line.actor, line.object_ref, line.data["fields"]), (operator, "stripe", ["stripe_webhook_secret"]))
        self.assertNotIn("whsec_new", json.dumps(line.data))

    def test_each_section_is_saved_on_its_own(self):
        operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.client.force_login(operator)
        url = reverse("backoffice:payment_settings")
        # Saving the bank section touches neither Stripe nor PayPal nor the invoice settings.
        self.client.post(url, {"section": "bank_transfer", "bank_transfer-offer": "on", "bank_transfer-bank_account_holder": "Zuri-AI GmbH",
                               "bank_transfer-iban": "de89 3704 0044 0532 0130 00", "bank_transfer-bic": "COBADEFFXXX", "bank_transfer-bank_name": "Commerzbank"})
        config = PaymentSettings.load()
        self.assertEqual((config.iban, config.stripe_secret_key, config.paypal_secret, config.tax_number), ("DE89 3704 0044 0532 0130 00", "sk_test_abc", "pp-secret", "035 123 45678"))
        # Unticking "offer" takes PayPal off the order page and keeps its keys.
        self.client.post(url, {"section": "paypal", "paypal-paypal_client_id": "pp-client", "paypal-paypal_sandbox": "on"})
        config = PaymentSettings.load()
        self.assertEqual((config.methods, config.paypal_secret, config.paypal_webhook_id), ("stripe,bank_transfer", "pp-secret", ""))
        self.assertEqual([m.code for m in usable_methods()], ["stripe", "bank_transfer", "sandbox"])
        # Removing a key is its own tick.
        self.client.post(url, {"section": "stripe", "stripe-offer": "on", "stripe-clear_stripe_secret_key": "on"})
        self.assertEqual(PaymentSettings.load().stripe_secret_key, "")
        self.client.post(url, {"section": "invoices", "invoices-invoice_prefix": "ZA", "invoices-tax_number": "", "invoices-vat_rate": "19"})
        self.assertEqual(PaymentSettings.load().invoice_prefix, "ZA")
        self.assertEqual(self.client.get(url).status_code, 200)


class SandboxTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)

    def buy(self, plan="professional"):
        response = self.client.post(reverse("billing:checkout_start", args=[plan]), {**ADDRESS, "method": "sandbox"})
        self.client.post(response.url, {"outcome": "pay"})
        return Order.objects.order_by("-pk").first()

    def test_the_switch_and_the_tester_list_decide_who_sees_the_sandbox(self):
        page = reverse("billing:checkout_start", args=["professional"])
        self.assertContains(self.client.get(page), "id_method_sandbox")
        self.client.force_login(self.operator)
        url = reverse("backoffice:payment_settings")
        self.assertContains(self.client.get(url), "The sandbox is on for every customer")
        self.client.post(url, {"section": "sandbox", "sandbox-sandbox_enabled": "on", "sandbox-sandbox_testers": "tester@example.com\n"})
        self.assertNotContains(self.client.get(url), "The sandbox is on for every customer")
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(page), "id_method_sandbox")
        with self.assertRaises(services.CheckoutError):  # not offered, and not accepted either
            services.start_checkout(self.account, self.user, Plan.objects.get(code="professional"), method="sandbox")
        tester, _account = make_customer(email="tester@example.com", plan="community", name="Tester GmbH")
        self.client.force_login(tester)
        self.assertContains(self.client.get(page), "id_method_sandbox")
        self.assertContains(self.client.get(reverse("billing:pricing")), "Test mode")
        self.client.force_login(self.operator)
        self.client.post(url, {"section": "sandbox", "sandbox-sandbox_testers": "tester@example.com"})
        self.client.force_login(tester)
        self.assertNotContains(self.client.get(page), "id_method_sandbox")

    def test_test_purchases_are_marked_and_stay_out_of_the_books(self):
        real = self.order()
        self.stripe_event("checkout.session.completed", self.session(real))
        test = self.buy(plan="business")
        self.assertTrue(test.is_test)
        self.assertFalse(Order.objects.get(pk=real.pk).is_test)
        year = timezone.localdate().year
        document = test.invoices.get()
        # Its own numbers: the real sequence has no gap and no test in it.
        self.assertEqual((document.number, document.is_test, real.invoices.get().number), (f"TEST-{year}-00001", True, f"RE-{year}-00001"))
        self.assertIn("[TEST]", mail.outbox[-1].subject)
        self.assertContains(self.client.get(reverse("billing:order", args=[test.ref])), "Test order")
        # A test order cannot be turned into a real one.
        pending = self.client.post(reverse("billing:checkout_start", args=["supporter"]), {**ADDRESS, "method": "sandbox"})
        open_order = Order.objects.order_by("-pk").first()
        self.assertEqual(pending.status_code, 302)
        with self.assertRaises(services.CheckoutError):
            services.pay(open_order, "bank_transfer", self.request())

        self.client.force_login(self.operator)
        today = timezone.localdate()
        listing = self.client.get(reverse("backoffice:invoices"))
        self.assertContains(listing, f"RE-{year}-00001")
        self.assertNotContains(listing, f"TEST-{year}-00001</a>")
        archive = zipfile.ZipFile(io.BytesIO(self.client.get(reverse("backoffice:invoice_export", args=[today.year, today.month])).content))
        self.assertEqual([n for n in archive.namelist() if n.startswith("belege/")], [f"belege/RE-{year}-00001.pdf"])
        self.assertNotIn("sandbox", archive.read("zahlungen.csv").decode())
        from apps.backoffice.views import status_snapshot

        self.assertEqual((status_snapshot()["paid_orders_30d"], status_snapshot()["revenue_eur_30d"]), (1, 100.0))
        self.assertContains(self.client.get(reverse("backoffice:orders") + "?status=test"), test.ref)
        self.assertNotContains(self.client.get(reverse("backoffice:orders") + "?status=test"), real.ref)

    def test_test_purchases_can_be_deleted_and_real_ones_stay(self):
        real = self.order()
        self.stripe_event("checkout.session.completed", self.session(real))
        _other, other_account = make_customer(email="second@example.com", plan="community", name="Second GmbH")
        test = self.buy(plan="business")  # replaces the real plan of this account with a test plan
        document = test.invoices.get()
        path = document.pdf.path
        self.assertEqual(current_entitlement(self.account).plan.code, "business")

        self.client.force_login(self.operator)
        url = reverse("backoffice:purge_test_orders")
        self.client.post(url)  # without the tick nothing happens
        self.assertEqual(Order.objects.filter(is_test=True).count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {"confirm": "on"})
        self.assertEqual(Order.objects.filter(is_test=True).count(), 0)
        self.assertFalse(Invoice.objects.filter(is_test=True).exists())
        self.assertFalse(Payment.objects.filter(provider="sandbox").exists())
        self.assertFalse(PaymentEvent.objects.filter(provider="sandbox").exists())
        import os

        self.assertFalse(os.path.exists(path))
        # The real order, its payment, history and invoice are untouched.
        real.refresh_from_db()
        self.assertEqual((real.status, real.invoices.count(), real.payments.get().status, real.payment_events.count()), ("paid", 1, "succeeded", 2))
        self.assertTrue(os.path.exists(real.invoices.get().pdf.path))
        # The account that held the test plan is back on the free one.
        self.assertEqual(current_entitlement(self.account).plan.code, "community")
        self.assertEqual(current_entitlement(other_account).plan.code, "community")
        line = AuditEvent.objects.get(action="billing.test_orders_purged")
        self.assertEqual((line.actor, line.data["orders"]), (self.operator, 1))


class StripeTests(PaymentTestCase):
    def test_checkout_goes_to_stripe_and_the_webhook_starts_the_plan(self):
        self.provider.on("POST", "/v1/checkout/sessions", {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"})
        response = self.client.post(reverse("billing:checkout_start", args=["professional"]), {**ADDRESS, "method": "stripe"})
        self.assertEqual((response.status_code, response.url), (302, "https://checkout.stripe.com/c/pay/cs_test_1"))
        order = Order.objects.get()
        (_m, _p, form, headers) = self.provider.called("POST", "/v1/checkout/sessions")[-1]
        self.assertEqual(headers["authorization"], "Bearer sk_test_abc")
        self.assertTrue(headers["idempotency-key"].startswith("checkout-"))
        self.assertEqual((form["mode"], form["line_items[0][price_data][unit_amount]"], form["client_reference_id"]), ("payment", "10000", order.ref))
        self.assertIn(reverse("billing:return", args=[order.payments.get().pk]), form["success_url"])
        self.assertEqual(current_entitlement(self.account).plan.code, "community")  # nothing is paid yet

        self.assertEqual(self.stripe_event("checkout.session.completed", self.session(order)).content, b"paid")
        self.assertEqual(self.stripe_event("checkout.session.completed", self.session(order)).content, b"duplicate")
        order.refresh_from_db()
        payment = order.payments.get(status="succeeded")
        self.assertEqual((order.status, order.payment_method, payment.provider_id, payment.provider_ref), ("paid", "stripe", "pi_1", "cs_test_1"))
        entitlement = current_entitlement(self.account)
        self.assertEqual((entitlement.plan.code, entitlement.valid_until), ("professional", timezone.localdate() + timedelta(days=365)))
        self.assertEqual(order.invoices.count(), 1)
        self.assertEqual([kind for kind in order.payment_events.values_list("kind", flat=True)], ["created", "payment_succeeded"])

    def test_a_forged_or_stale_webhook_is_rejected_and_recorded(self):
        order = self.order()
        body = json.dumps({"id": "evt_x", "type": "checkout.session.completed", "data": {"object": self.session(order)}}).encode()
        url = reverse("billing:webhook", args=["stripe"])
        forged = self.client.post(url, body, content_type="application/json", headers={"Stripe-Signature": f"t={int(time.time())},v1={'0' * 64}"})
        old = int(time.time()) - 3600
        signature = hmac.new(WHSEC.encode(), f"{old}.".encode() + body, hashlib.sha256).hexdigest()
        stale = self.client.post(url, body, content_type="application/json", headers={"Stripe-Signature": f"t={old},v1={signature}"})
        self.assertEqual((forged.status_code, stale.status_code), (400, 400))
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")
        self.assertEqual(PaymentEvent.objects.filter(kind="rejected").count(), 2)

    def test_the_return_from_stripe_confirms_without_waiting_for_the_webhook(self):
        order = self.order()
        self.provider.on("GET", "/v1/checkout/sessions/cs_test_1", self.session(order))
        response = self.client.get(reverse("billing:return", args=[order.payments.get().pk]))
        self.assertRedirects(response, reverse("billing:order", args=[order.ref]))
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        # The webhook that follows changes nothing.
        self.assertEqual(self.stripe_event("checkout.session.completed", self.session(order)).content, b"duplicate")
        self.assertEqual(Invoice.objects.count(), 1)

    def test_a_different_amount_is_not_accepted(self):
        order = self.order()
        self.assertEqual(self.stripe_event("checkout.session.completed", self.session(order, amount_total=100)).content, b"amount_mismatch")
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")
        self.assertEqual(ReconciliationIssue.objects.get().kind, "amount")
        self.assertIn("different amount", mail.outbox[-1].subject)

    def test_an_expired_session_fails_the_order(self):
        order = self.order()
        self.stripe_event("checkout.session.expired", {**self.session(order), "status": "expired", "payment_status": "unpaid"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.subscription.status), ("failed", "ended"))

    def test_refund_goes_back_through_stripe_with_a_credit_note_and_ends_the_plan(self):
        order = self.order()
        self.stripe_event("checkout.session.completed", self.session(order))
        self.provider.on("POST", "/v1/refunds", {"id": "re_1"})
        operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.client.force_login(operator)
        url = reverse("backoffice:order", args=[order.ref])
        self.client.post(url, {"action": "refund", "amount": "25,00", "reason": "Goodwill"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.refunded_total), ("partially_refunded", Decimal("25.00")))
        form = self.provider.called("POST", "/v1/refunds")[-1][2]
        self.assertEqual((form["payment_intent"], form["amount"]), ("pi_1", "2500"))
        note = order.invoices.get(kind="credit_note")
        self.assertEqual((note.gross_total, note.refers_to, note.net_total + note.tax_total), (Decimal("-25.00"), order.invoices.get(kind="invoice"), Decimal("-25.00")))
        self.assertEqual(current_entitlement(self.account).plan.code, "professional")
        # Stripe tells us about our own refund: nothing is booked twice.
        self.assertEqual(self.stripe_event("refund.created", {"id": "re_1", "payment_intent": "pi_1", "amount": 2500}, "evt_r1").content, b"refund_confirmed")

        self.provider.on("POST", "/v1/refunds", {"id": "re_2"})
        self.client.post(url, {"action": "refund", "amount": "", "reason": "Withdrawal"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.refunded_total), ("refunded", Decimal("100.00")))
        self.assertEqual(current_entitlement(self.account).plan.code, "community")
        self.assertEqual(order.invoices.filter(kind="credit_note").count(), 2)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_a_refund_made_in_the_stripe_dashboard_is_booked_and_flagged(self):
        order = self.order()
        self.stripe_event("checkout.session.completed", self.session(order))
        self.stripe_event("refund.created", {"id": "re_ext", "payment_intent": "pi_1", "amount": 10000, "reason": "requested_by_customer"}, "evt_r")
        order.refresh_from_db()
        self.assertEqual(order.status, "refunded")
        self.assertEqual(ReconciliationIssue.objects.get().kind, "external_refund")

    def test_a_dispute_alerts_the_team(self):
        order = self.order()
        self.stripe_event("checkout.session.completed", self.session(order))
        self.stripe_event("charge.dispute.created", {"id": "dp_1", "payment_intent": "pi_1", "amount": 10000, "reason": "fraudulent"}, "evt_d1")
        self.assertEqual(order.payments.get().status, "disputed")
        self.assertIn("dispute", mail.outbox[-1].subject.lower())
        self.stripe_event("charge.dispute.closed", {"id": "dp_1", "payment_intent": "pi_1", "amount": 10000, "status": "lost"}, "evt_d2")
        self.assertEqual(order.payments.get().status, "charged_back")

    def test_webhook_set_up_from_the_backoffice_stores_the_signing_secret(self):
        self.provider.on("GET", "/v1/webhook_endpoints", {"data": []})
        self.provider.on("POST", "/v1/webhook_endpoints", {"id": "we_1", "secret": "whsec_fresh"})
        self.assertEqual(stripe.set_up_webhook(self.config, "https://cassilion.com/billing/webhook/stripe/"), "we_1")
        self.assertEqual(PaymentSettings.load().stripe_webhook_secret, "whsec_fresh")

    def test_reconciliation_finds_what_we_missed(self):
        order = self.order()
        self.stripe_event("checkout.session.completed", self.session(order))
        other = self.order(plan="business")
        self.config.paypal_client_id = ""
        self.config.save()
        self.provider.on("GET", "/v1/payment_intents", {"data": [
            {"id": "pi_1", "status": "succeeded", "amount_received": 9900, "metadata": {"order": order.ref}},
            {"id": "pi_2", "status": "succeeded", "amount_received": 100000, "metadata": {"order": other.ref}},
            {"id": "pi_other", "status": "succeeded", "amount_received": 500, "metadata": {}},
        ], "has_more": False})
        self.assertEqual(payments.reconcile(), 2)
        self.assertEqual(sorted(ReconciliationIssue.objects.values_list("kind", "provider_id")), [("amount", "pi_1"), ("missing_local", "pi_2")])
        self.assertEqual(payments.reconcile(), 0)  # each difference is listed once


class PayPalTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.provider.on("POST", "/v1/oauth2/token", {"access_token": "tok", "expires_in": 3600})

    def test_approval_is_captured_when_the_customer_returns(self):
        self.provider.on("POST", "/v2/checkout/orders", {"id": "PP-ORDER", "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=PP-ORDER"}]})
        order = self.order(method="paypal")
        self.assertIn("paypal.com", self.url)
        sent = json.loads(self.provider.called("POST", "/v2/checkout/orders")[-1][2])
        self.assertEqual(sent["purchase_units"][0]["amount"], {"currency_code": "EUR", "value": "100.00"})
        self.assertEqual(sent["purchase_units"][0]["custom_id"], order.ref)
        self.provider.on("POST", "/v2/checkout/orders/PP-ORDER/capture", {"id": "PP-ORDER", "purchase_units": [
            {"custom_id": order.ref, "payments": {"captures": [{"id": "CAP-1", "status": "COMPLETED", "amount": {"value": "100.00"}}]}}]})
        self.client.get(reverse("billing:return", args=[order.payments.get().pk]))
        order.refresh_from_db()
        self.assertEqual((order.status, order.payments.get().provider_id, order.payment_method), ("paid", "CAP-1", "paypal"))

    def test_webhooks_are_verified_with_paypal(self):
        self.provider.on("POST", "/v2/checkout/orders", {"id": "PP-ORDER", "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/x"}]})
        order = self.order(method="paypal")
        event = {"id": "WH-EVT", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {
            "id": "CAP-9", "custom_id": order.ref, "amount": {"value": "100.00"}, "supplementary_data": {"related_ids": {"order_id": "PP-ORDER"}}}}
        headers = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert", "PAYPAL-TRANSMISSION-ID": "t",
                   "PAYPAL-TRANSMISSION-SIG": "s", "PAYPAL-TRANSMISSION-TIME": "now"}
        url = reverse("billing:webhook", args=["paypal"])
        self.provider.on("POST", "/v1/notifications/verify-webhook-signature", {"verification_status": "FAILURE"})
        self.assertEqual(self.client.post(url, json.dumps(event), content_type="application/json", headers=headers).status_code, 400)
        foreign = {**headers, "PAYPAL-CERT-URL": "https://paypal.com.evil.example/cert"}
        self.assertEqual(self.client.post(url, json.dumps(event), content_type="application/json", headers=foreign).status_code, 400)
        self.provider.on("POST", "/v1/notifications/verify-webhook-signature", {"verification_status": "SUCCESS"})
        self.assertEqual(self.client.post(url, json.dumps(event), content_type="application/json", headers=headers).content, b"paid")
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")


class BankTransferTests(PaymentTestCase):
    def test_order_waits_for_the_money_and_a_statement_books_it(self):
        response = self.client.post(reverse("billing:checkout_start", args=["professional"]), {**ADDRESS, "method": "bank_transfer"})
        order = Order.objects.get()
        self.assertRedirects(response, reverse("billing:order", args=[order.ref]))
        self.assertRegex(order.ref, r"^CO-[A-HJ-NP-Z2-9]{7}$")
        self.assertEqual(order.due_date, timezone.localdate() + timedelta(days=14))
        instructions = mail.outbox[-1]
        for expected in [order.ref, "DE02 1203 0000 0000 2020 51", "100.00"]:
            self.assertIn(expected, instructions.body)
        self.assertContains(self.client.get(response.url), "DE02 1203 0000 0000 2020 51")
        self.assertEqual(current_entitlement(self.account).plan.code, "community")
        self.assertEqual(order.invoices.count(), 0)

        spaced = order.ref[:6] + " " + order.ref[6:]
        statement = (
            "Buchungstag;Betrag;Verwendungszweck;Name\n"
            f"20.11.2026;100,00;Rechnung {spaced} danke;Example GmbH\n"
            "20.11.2026;50,00;irgendwas;Jemand\n"
            "20.11.2026;-12,00;Miete;Vermieter\n"
        ).encode()
        report = payments.import_statement(statement)
        self.assertEqual((len(report.matched), len(report.unmatched), len(report.problems)), (1, 1, 0))
        order.refresh_from_db()
        self.assertEqual((order.status, current_entitlement(self.account).plan.code), ("paid", "professional"))
        again = payments.import_statement(statement)
        self.assertEqual((len(again.matched), len(again.problems)), (0, 1))  # already paid: nothing twice

    def test_camt_statement_and_wrong_amount(self):
        order = self.order(method="bank_transfer")
        camt = f"""<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt>
            <Ntry><Amt Ccy="EUR">99.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>2026-11-20</Dt></BookgDt>
            <NtryDtls><TxDtls><RltdPties><Dbtr><Nm>Example GmbH</Nm></Dbtr></RltdPties><RmtInf><Ustrd>{order.ref}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
            </Stmt></BkToCstmrStmt></Document>""".encode()
        report = payments.import_statement(camt)
        self.assertEqual(len(report.problems), 1)
        self.assertIn("expected 100.00, got 99.00", report.problems[0][1])
        with self.assertRaises(payments.StatementError):
            payments.parse_statement(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')

    def test_the_team_marks_an_order_paid_and_an_unpaid_one_is_given_up(self):
        order = self.order(method="bank_transfer")
        operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.client.force_login(operator)
        self.client.post(reverse("backoffice:order", args=[order.ref]), {"action": "mark_paid"})
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        event = order.payment_events.get(kind="marked_paid")
        self.assertEqual((event.source, event.actor), ("staff", operator))

        late = self.order(plan="business", method="bank_transfer")
        services.expire_due(today=late.due_date + timedelta(days=1))
        late.refresh_from_db()
        self.assertEqual((late.status, late.payments.get().status), ("cancelled", "cancelled"))
        # The money arrives after all: nobody gets a plan by accident, a person decides.
        payments.confirm_bank_payment(late)
        self.assertEqual(ReconciliationIssue.objects.get().kind, "late_payment")
        self.assertEqual(current_entitlement(self.account).plan.code, "professional")


class InvoiceTests(PaymentTestCase):
    def paid(self, plan="professional"):
        order = self.order(plan=plan)
        self.stripe_event("checkout.session.completed", self.session(order), f"evt_{plan}")
        return Order.objects.get(pk=order.pk)

    def test_invoice_has_gapless_number_german_vat_and_the_e_invoice_inside(self):
        first = self.paid().invoices.get()
        year = timezone.localdate().year
        self.assertEqual(first.number, f"RE-{year}-00001")
        self.assertEqual((first.gross_total, first.net_total, first.tax_total), (Decimal("100.00"), Decimal("84.03"), Decimal("15.97")))
        self.assertEqual(first.recipient["lines"], ["Example GmbH", "Dana Demo", "Beispielweg 1", "60311 Frankfurt am Main"])
        self.assertEqual(first.seller["tax_number"], "035 123 45678")
        pdf = first.read()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), first.pdf_sha256)
        from facturx import get_xml_from_pdf

        _name, xml = get_xml_from_pdf(pdf, check_xsd=True)
        for expected in [first.number, "84.03", "15.97", "<ram:RateApplicablePercent>19.00", "<ram:DuePayableAmount>0.00", "Example GmbH"]:
            self.assertIn(expected, xml.decode())
        with self.assertRaises(ImmutableError):
            first.save()
        with self.assertRaises(ImmutableError):
            first.delete()

    def test_small_business_shows_no_vat(self):
        self.config.small_business = True
        self.config.save()
        invoice = self.paid().invoices.get()
        self.assertEqual((invoice.net_total, invoice.tax_total, invoice.small_business), (Decimal("100.00"), Decimal("0.00"), True))

    def test_customer_sees_own_documents_only(self):
        invoice = self.paid().invoices.get()
        page = self.client.get(reverse("billing:overview"))
        self.assertContains(page, invoice.number)
        response = self.client.get(reverse("billing:invoice", args=[invoice.number]))
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"))
        stranger, _account = make_customer(email="eve@example.com", plan="community", name="Other GmbH")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse("billing:invoice", args=[invoice.number])).status_code, 404)
        self.assertEqual(self.client.get(reverse("billing:order", args=[invoice.order.ref])).status_code, 404)
        self.assertEqual(self.client.get(reverse("backoffice:invoice_pdf", args=[invoice.number])).status_code, 302)

    def test_a_new_recipient_voids_and_replaces_the_invoice(self):
        order = self.paid()
        invoice = order.invoices.get()
        operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.client.force_login(operator)
        self.client.post(reverse("backoffice:order", args=[order.ref]), {
            "action": "correct_invoice", "number": invoice.number, "company": "Example Holding AG", "street": "Neue Str. 2",
            "postal_code": "10115", "city": "Berlin", "country": "DE", "reason": "Company address"})
        void, replacement = order.invoices.exclude(pk=invoice.pk).order_by("number")
        self.assertEqual((void.purpose, void.gross_total, replacement.purpose, replacement.gross_total), ("cancellation", Decimal("-100.00"), "correction", Decimal("100.00")))
        self.assertEqual(replacement.recipient["lines"][0], "Example Holding AG")
        self.assertEqual(invoicing.invoice_for(order), replacement)
        self.assertEqual([n[-5:] for n in order.invoices.order_by("number").values_list("number", flat=True)], ["00001", "00002", "00003"])
        self.assertIn(replacement.number, mail.outbox[-1].subject)
        with self.assertRaises(invoicing.CorrectionError):
            invoicing.void_invoice(invoice, reason="again")

    def test_month_export_for_the_tax_adviser(self):
        invoice = self.paid().invoices.get()
        operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.client.force_login(operator)
        today = timezone.localdate()
        self.assertContains(self.client.get(reverse("backoffice:invoices")), invoice.number)
        response = self.client.get(reverse("backoffice:invoice_export", args=[today.year, today.month]))
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(sorted(archive.namelist()), ["EXTF_Buchungsstapel.csv", "LIESMICH.txt", "belege/" + invoice.number + ".pdf", "journal.csv", "zahlungen.csv"])
        self.assertIn(f"{invoice.number};Rechnung;", archive.read("journal.csv").decode())
        self.assertIn('"100,00";"S";"EUR"', archive.read("EXTF_Buchungsstapel.csv").decode())
        self.assertIn("1360", archive.read("EXTF_Buchungsstapel.csv").decode())  # Stripe money sits on the clearing account


class RenewalTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        order = self.order()
        self.stripe_event("checkout.session.completed", self.session(order))
        self.subscription = Subscription.objects.get()
        self.end = self.subscription.period_end
        mail.outbox.clear()

    def test_renewal_invoice_goes_out_before_the_term_ends_and_payment_extends_it(self):
        self.assertEqual(services.renew_due(today=self.end - timedelta(days=31)), 0)
        self.assertEqual(services.renew_due(today=self.end - timedelta(days=30)), 1)
        self.assertEqual(services.renew_due(today=self.end - timedelta(days=29)), 0)  # once
        renewal = Order.objects.get(kind="renewal")
        invoice = renewal.invoices.get()
        self.assertEqual((renewal.status, renewal.due_date, invoice.due_date, invoice.service_from), ("pending", self.end, self.end, self.end))
        notice = mail.outbox[-1]
        self.assertIn(invoice.number, notice.subject)
        self.assertIn(reverse("billing:order", args=[renewal.ref]), notice.body)
        self.assertEqual(notice.attachments[0][0], f"{invoice.number}.pdf")
        from facturx import get_xml_from_pdf

        xml = get_xml_from_pdf(invoice.read(), check_xsd=True)[1].decode()
        self.assertIn("<ram:DuePayableAmount>100.00", xml)
        self.assertNotIn("TotalPrepaidAmount", xml)
        self.assertIn("DE02120300000000202051", xml)
        self.assertContains(self.client.get(reverse("billing:order", args=[renewal.ref])), "pay another way")

        payments.import_statement(f"Datum;Betrag;Verwendungszweck\n01.11.2027;100,00;{renewal.ref}\n".encode())
        self.subscription.refresh_from_db()
        self.assertEqual((self.subscription.status, self.subscription.period_end), ("active", self.end + timedelta(days=365)))
        self.assertEqual(current_entitlement(self.account).valid_until, self.end + timedelta(days=365))
        self.assertEqual(renewal.invoices.count(), 1)  # the invoice sent with the notice is the invoice

    def test_unpaid_renewal_is_reminded_then_ends_and_is_left_to_a_person(self):
        services.renew_due(today=self.end - timedelta(days=30))
        renewal = Order.objects.get(kind="renewal")
        self.assertEqual(services.expire_due(today=self.end), 0)
        self.assertEqual(services.expire_due(today=self.end + timedelta(days=1)), 0)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "past_due")
        self.assertIn("reminder", mail.outbox[-1].subject.lower())
        reminders = len(mail.outbox)
        self.assertEqual(services.expire_due(today=self.end + timedelta(days=14)), 0)
        self.assertEqual(len(mail.outbox), reminders)  # one reminder
        self.assertEqual(services.expire_due(today=self.end + timedelta(days=15)), 1)
        self.subscription.refresh_from_db()
        self.assertEqual((self.subscription.status, ReconciliationIssue.objects.get().kind), ("ended", "unpaid_invoice"))
        self.assertFalse(invoicing.is_voided(renewal.invoices.get(kind="invoice")))

    def test_cancelling_voids_the_renewal_invoice(self):
        services.renew_due(today=self.end - timedelta(days=30))
        renewal = Order.objects.get(kind="renewal")
        self.client.post(reverse("billing:cancel"), {"subscription": self.subscription.pk})
        renewal.refresh_from_db()
        self.assertEqual(renewal.status, "cancelled")
        self.assertTrue(invoicing.is_voided(renewal.invoices.get(kind="invoice")))
        self.assertEqual(services.renew_due(today=self.end - timedelta(days=5)), 0)
        self.assertEqual(services.expire_due(today=self.end + timedelta(days=1)), 1)
        self.assertEqual(current_entitlement(self.account).plan.code, "community")

    def test_a_new_price_applies_to_the_next_renewal(self):
        Plan.objects.filter(code="professional").update(price_eur_year=120)
        services.renew_due(today=self.end - timedelta(days=30))
        self.assertEqual(Order.objects.get(kind="renewal").total, Decimal("120.00"))


class ProductTests(PaymentTestCase):
    def test_three_products_each_with_its_own_license(self):
        self.assertEqual(list(Product.objects.values_list("code", "license_prefix")), [("cassilion", "CAS"), ("zuri-ai", "ZUR"), ("vakaluja", "VAK")])
        # Hidden products are not on the plans page and cannot be ordered.
        self.assertNotContains(self.client.get(reverse("billing:pricing")), "Vakaluja")
        self.assertEqual(self.client.post(reverse("billing:free_plan", args=["vakaluja-community"])).status_code, 404)

        vakaluja = Product.objects.get(code="vakaluja")
        vakaluja.is_public = True
        vakaluja.save()
        team = Plan.objects.create(product=vakaluja, code="vakaluja-team", name="Team", price_eur_year=500, support_level="ai_human", ticket_quota_month=5, rank=1)
        self.assertContains(self.client.get(reverse("billing:pricing")), "Vakaluja")
        self.client.post(reverse("billing:free_plan", args=["vakaluja-community"]))
        held = {e.product.code: e for e in entitlements(self.account)}
        self.assertEqual({code: e.plan.code for code, e in held.items()}, {"cassilion": "community", "vakaluja": "vakaluja-community"})
        license = held["vakaluja"].licenses.get()
        self.assertTrue(license.license_id.startswith("VAK-"))
        self.assertEqual(license.payload["product"], "vakaluja")

        # Buying a plan of one product leaves the other product alone.
        self.provider.on("POST", "/v1/checkout/sessions", {"id": "cs_test_1", "url": "https://checkout.stripe.com/x"})
        order, _url = services.start_checkout(self.account, self.user, team, self.request(), method="stripe")
        self.stripe_event("checkout.session.completed", self.session(order))
        held = {e.product.code: e.plan.code for e in entitlements(self.account)}
        self.assertEqual(held, {"cassilion": "community", "vakaluja": "vakaluja-team"})
        self.assertIn("Vakaluja Team", order.invoices.get().lines[0]["description"])
        self.assertContains(self.client.get(reverse("licensing:licenses")), "vakaluja.lic")
        self.assertContains(self.client.get(reverse("billing:overview")), "Vakaluja")

    def test_products_and_plans_are_edited_in_the_backoffice(self):
        self.assertEqual(self.client.get(reverse("backoffice:products")).status_code, 302)  # customers have no business here
        operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.client.force_login(operator)
        self.assertContains(self.client.get(reverse("backoffice:products")), "Zuri-AI")
        zuri = Product.objects.get(code="zuri-ai")
        response = self.client.post(reverse("backoffice:plan_new"), {
            "product": zuri.pk, "name": "Professional", "code": "zuri-ai-professional", "price_eur_year": 250, "support_level": "ai_human",
            "ticket_quota_month": 5, "bug_report_quota_month": 10, "self_service": "on", "is_public": "on", "rank": 2})
        self.assertRedirects(response, reverse("backoffice:products"))
        self.assertEqual(Plan.objects.get(code="zuri-ai-professional").price_eur_year, 250)
        line = AuditEvent.objects.filter(action="plan.saved").get()
        self.assertEqual((line.actor, line.object_ref), (operator, "zuri-ai-professional"))
        for name in ["orders", "invoices", "payment_issues", "bank_import", "payment_settings", "dashboard"]:
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}")).status_code, 200, name)
