"""HTTP for the payment providers: one client, a clear error, and a seam for tests."""

from __future__ import annotations

import httpx

# Tests (and the console's connection check in tests) put an ``httpx.MockTransport`` here.
TRANSPORT: httpx.BaseTransport | None = None
TIMEOUT = 20.0


class ProviderError(Exception):
    """The provider refused or could not be reached; the message is safe to show staff."""


def client(**kwargs) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=TRANSPORT, **kwargs)


def check(response: httpx.Response, provider: str) -> dict:
    """The JSON body of a successful response, else :class:`ProviderError` with the reason."""
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.is_success:
        return data
    detail = (
        (data.get("error") or {}).get("message")  # Stripe
        or data.get("message")  # PayPal
        or data.get("error_description")  # PayPal OAuth
        or response.reason_phrase
    )
    raise ProviderError(f"{provider} {response.status_code}: {detail}")
