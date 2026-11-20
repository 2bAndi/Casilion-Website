from django.conf import settings

from .base import PaymentProvider, ProviderEvent, WebhookError  # noqa: F401
from .sandbox import SandboxProvider

_REGISTRY = {"sandbox": SandboxProvider}


def get_provider(name: str | None = None) -> PaymentProvider:
    name = name or settings.CASSILION["PAYMENT_PROVIDER"]
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise WebhookError(f"Unknown payment provider: {name}") from None
