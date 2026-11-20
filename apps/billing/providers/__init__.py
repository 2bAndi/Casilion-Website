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
        return bool(cfg.sandbox_enabled)
    return False


def real_provider_connected() -> bool:
    """Is a payment service provider set up? The privacy notice says so."""
    cfg = config()
    return is_configured("stripe", cfg) or is_configured("paypal", cfg)


def usable_methods(user=None) -> list[PaymentProvider]:
    """What this login can pay with right now, in the order shown on the order page.

    The sandbox is switched on in the backoffice and can be limited to named testers there.
    Without ``user`` the question is what is switched on at all.
    """
    cfg, registry = config(), _registry()
    methods = [registry[code] for code in cfg.method_list if code in registry and code != "sandbox" and is_configured(code, cfg)]
    if cfg.sandbox_for(user):
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
