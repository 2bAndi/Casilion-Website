from django.conf import settings


def site(request):
    """Values every template needs."""
    account = getattr(request, "account", None) if request.user.is_authenticated else None
    return {
        "SITE_URL": settings.CASSILION["SITE_URL"],
        "DEBUG": settings.DEBUG,
        "SITE_NOINDEX": settings.SITE_NOINDEX,
        "current_account": account,
        "sandbox_payments": settings.CASSILION["PAYMENT_PROVIDER"] == "sandbox",
    }
