"""Shared helpers for the test suite."""
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from apps.accounts.models import Account, Address, User
from apps.accounts.services import create_account
from apps.billing import services as billing
from apps.billing.models import Plan
from apps.billing.providers import get_provider
from apps.billing.providers.base import EVENT_PAID
from apps.support import services as support
from apps.support.models import AIJob

_TMP = tempfile.mkdtemp(prefix="cassilion-test-")

# Signing keys and release files of the test run never touch the developer's var/ directory.
test_settings = override_settings(
    CASSILION={
        **settings.CASSILION,
        "LICENSE_KEY_DIR": Path(_TMP) / "keys",
        "RELEASES_DIR": Path(_TMP) / "releases",
        "INVOICE_DIR": Path(_TMP) / "invoices",
        "LICENSE_AUTOCREATE_KEY": True,
        "SANDBOX_PAYMENTS": True,
        "SANDBOX_WEBHOOK_SECRET": "test-sandbox-secret",
    },
    # Django's test runner swaps in the in-memory backend; put the mail log back in front of it.
    EMAIL_BACKEND="apps.mailing.backend.LoggingBackend",
    MAIL_DELIVERY_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


# What the order page asks for besides the payment method: the invoice address.
ADDRESS = {"line1": "Beispielweg 1", "postal_code": "60311", "city": "Frankfurt am Main"}


def make_customer(email="dana@example.com", plan="professional", kind=Account.Kind.COMPANY, name="Example GmbH"):
    """A user with an account on the given plan, bought the way a customer would buy it."""
    user = User.objects.create_user(email, "a-long-test-password", full_name="Dana Demo")
    account = create_account(user, kind=kind, name=name, country="DE")
    Address.objects.filter(account=account).update(**ADDRESS)
    if plan != "community":
        order, _url = billing.start_checkout(account, user, Plan.objects.get(code=plan))
        body, headers = get_provider("sandbox").build_event(order, EVENT_PAID)
        billing.handle_webhook("sandbox", body, headers)
    return user, account


def run_worker_once(pipeline=None, worker_id="test-worker"):
    """What the AI worker does for one job, without HTTP: claim, analyse, post the result."""
    from ai_worker.config import load
    from ai_worker.pipeline import Pipeline

    job = support.claim_job(worker_id)
    if job is None:
        return None
    payload = support.build_payload(job)
    result = (pipeline or Pipeline(load())).run(payload)
    support.submit_result(AIJob.objects.select_related("ticket").get(pk=job.pk), result)
    return payload, result


@test_settings
class CassilionTestCase(TestCase):
    pass
