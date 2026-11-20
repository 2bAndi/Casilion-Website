from django.conf import settings

from .base import PaymentProvider, RefundResult, StartResult, WebhookError, WebhookEvent, WebhookRejected  # noqa: F401
from .http import ProviderError  # noqa: F401


def config():
    """The payment accounts as entered in the backoffice."""
    from apps.billing.models import PaymentSettings

    return PaymentSettings.load()


def _registry() -> dict[str, PaymentProvider]:
    from . import bank, paypal, sandbox, stripe

    return {
        "stripe": stripe.StripeCard(),
        "paypal": paypal.PayPal(),
        "bank_transfer": bank.BankTransfer(),
        "sandbox": sandbox.SandboxProvider(),
    }


def is_configured(code: str, cfg=None) -> bool:
    cfg = cfg or config()
    if code == "stripe":
        return bool(cfg.stripe_secret_key)
    if code == "paypal":
        return bool(cfg.paypal_client_id and cfg.paypal_secret)
    if code == "bank_transfer":
        return bool(cfg.iban)
    if code == "sandbox":
        return bool(settings.CASSILION["SANDBOX_PAYMENTS"])
    return False


def usable_methods() -> list[PaymentProvider]:
    """What a customer can pay with right now, in the order shown on the order page."""
    cfg, registry = config(), _registry()
    methods = [registry[code] for code in cfg.method_list if code in registry and code != "sandbox" and is_configured(code, cfg)]
    if is_configured("sandbox", cfg):
        methods.append(registry["sandbox"])
    return methods


def get_provider(code: str | None = None) -> PaymentProvider:
    if code is None:
        methods = usable_methods()
        if not methods:
            raise WebhookRejected("No payment method is configured.")
        return methods[0]
    try:
        return _registry()[code]
    except KeyError:
        raise WebhookRejected(f"Unknown payment provider: {code}") from None
