from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, User
from apps.billing import services as billing
from apps.billing.models import Order, PaymentEvent, Plan, Subscription
from apps.billing.providers import get_provider
from apps.billing.providers.base import EVENT_PAID
from apps.licensing import codec, keys
from apps.licensing.models import License
from apps.licensing.services import current_entitlement, license_file

from .base import ADDRESS, CassilionTestCase, make_customer


class SignupTests(CassilionTestCase):
    def test_signup_creates_account_and_community_license(self):
        response = self.client.post(
            reverse("account_signup"),
            {
                "full_name": "Ada Example",
                "email": "ada@example.com",
                "kind": "company",
                "account_name": "Example Oy",
                "country": "FI",
                "password1": "a-long-test-password",
                "password2": "a-long-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="ada@example.com")
        account = user.accounts.get()
        self.assertEqual((account.name, account.kind, str(account.country)), ("Example Oy", Account.Kind.COMPANY, "FI"))
        entitlement = current_entitlement(account)
        self.assertEqual(entitlement.plan.code, "community")
        license = entitlement.licenses.get()
        payload = codec.verify(license_file(license), keys.public_keys())
        self.assertEqual(payload["plan"], "community")
        self.assertEqual(payload["licensee"], "Example Oy")

    def test_company_needs_a_name(self):
        response = self.client.post(
            reverse("account_signup"),
            {"full_name": "Ada", "email": "ada@example.com", "kind": "company", "country": "FI", "password1": "a-long-test-password", "password2": "a-long-test-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ada@example.com").exists())


class LicenseTests(CassilionTestCase):
    def test_tampered_license_is_refused(self):
        _user, account = make_customer(plan="community")
        text = license_file(current_entitlement(account).licenses.get())
        payload, _signed, _signature = codec.decode_file(text)
        payload["plan"] = "business"
        forged = codec.encode_file(payload, text.splitlines()[1].split(".")[1])
        with self.assertRaises(codec.LicenseError):
            codec.verify(forged, keys.public_keys())
        self.assertEqual(codec.verify(text, keys.public_keys())["plan"], "community")


class CheckoutTests(CassilionTestCase):
    def setUp(self):
        self.user, self.account = make_customer(plan="community")
        self.client.force_login(self.user)

    def test_sandbox_purchase_upgrades_the_license(self):
        response = self.client.post(reverse("billing:checkout_start", args=["professional"]), ADDRESS)
        order = Order.objects.get()
        self.assertRedirects(response, reverse("billing:sandbox_checkout", args=[order.ref]))
        # Opening the checkout page changes nothing: only the provider's event does.
        self.client.get(response.url)
        self.assertEqual(current_entitlement(self.account).plan.code, "community")

        self.client.post(response.url, {"outcome": "pay"})
        entitlement = current_entitlement(self.account)
        self.assertEqual(entitlement.plan.code, "professional")
        self.assertEqual(entitlement.valid_until, timezone.localdate() + timedelta(days=365))
        self.assertEqual(License.objects.filter(entitlement__account=self.account, revoked_at__isnull=True).count(), 1)
        self.assertEqual(Subscription.objects.get().status, Subscription.Status.ACTIVE)
        self.assertEqual(len(mail.outbox), 1)

    def test_failed_payment_changes_nothing(self):
        response = self.client.post(reverse("billing:checkout_start", args=["supporter"]), ADDRESS)
        self.client.post(response.url, {"outcome": "fail"})
        self.assertEqual(current_entitlement(self.account).plan.code, "community")
        self.assertEqual(Order.objects.get().status, Order.Status.FAILED)

    def test_webhook_is_idempotent_and_signed(self):
        order, _url = billing.start_checkout(self.account, self.user, Plan.objects.get(code="business"))
        body, headers = get_provider("sandbox").build_event(order, EVENT_PAID)
        url = reverse("billing:webhook", args=["sandbox"])
        ok = self.client.post(url, body, content_type="application/json", headers=headers)
        again = self.client.post(url, body, content_type="application/json", headers=headers)
        self.assertEqual((ok.status_code, again.status_code), (200, 200))
        self.assertEqual(PaymentEvent.objects.filter(source="webhook").count(), 1)
        self.assertEqual(self.account.entitlements.filter(plan__code="business").count(), 1)

        forged = self.client.post(url, body, content_type="application/json", headers={"X-Sandbox-Signature": "0" * 64})
        self.assertEqual(forged.status_code, 400)

    def test_enterprise_is_not_sold_in_the_shop(self):
        with self.assertRaises(billing.CheckoutError):
            billing.start_checkout(self.account, self.user, Plan.objects.get(code="enterprise"))

    def test_cancelled_plan_returns_to_community_after_the_period(self):
        order, _url = billing.start_checkout(self.account, self.user, Plan.objects.get(code="supporter"))
        body, headers = get_provider("sandbox").build_event(order, EVENT_PAID)
        billing.handle_webhook("sandbox", body, headers)
        subscription = Subscription.objects.get()
        billing.cancel(subscription, self.user)
        self.assertEqual(billing.expire_due(), 0)  # still inside the paid period
        self.assertEqual(current_entitlement(self.account).plan.code, "supporter")
        self.assertEqual(billing.expire_due(today=subscription.period_end + timedelta(days=1)), 1)
        self.assertEqual(current_entitlement(self.account).plan.code, "community")
