from django.conf import settings
from django.utils.functional import SimpleLazyObject


def _no_provider() -> bool:
    from apps.billing.providers import real_provider_connected

    return not real_provider_connected()


def site(request):
    """Values every template needs."""
    account = getattr(request, "account", None) if request.user.is_authenticated else None
    return {
        "SITE_URL": settings.CASSILION["SITE_URL"],
        "DEBUG": settings.DEBUG,
        "SITE_NOINDEX": settings.SITE_NOINDEX,
        "current_account": account,
        # True while neither Stripe nor PayPal is set up (the privacy notice says so). Looked up only where a template asks.
        "sandbox_payments": SimpleLazyObject(_no_provider),
        "company": settings.COMPANY,
    }
