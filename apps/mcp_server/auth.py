"""Token checking for the MCP endpoints, and the bridge from async tools to the (sync) service layer."""
from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections, connection
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from . import tokens
from .models import AgentToken

SCOPE = "mcp:use"


def _fresh_connection() -> None:
    """What Django does around every web request, done here for MCP calls: drop stale database connections.

    Skipped inside a transaction (the test runner wraps each test in one).
    """
    if not connection.in_atomic_block:
        close_old_connections()


def resource_url(kind: str) -> str:
    path = "/mcp" if kind == AgentToken.Kind.CUSTOMER else "/ops/mcp"
    return settings.CASSILION["SITE_URL"] + path


class AgentTokenVerifier:
    """Implements the SDK's TokenVerifier protocol on top of our token table.

    A token is only valid at the endpoint of its own kind: a customer token
    presented to the operator endpoint is refused here and again by the
    resource check of the SDK's bearer middleware.
    """

    def __init__(self, kind: str):
        self.kind = kind

    async def verify_token(self, token: str) -> AccessToken | None:
        record = await sync_to_async(self._lookup)(token)
        if record is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"agent-token-{record.pk}",
            scopes=[SCOPE],
            expires_at=int(record.expires_at.timestamp()) if record.expires_at else None,
            resource=resource_url(self.kind),
            subject=str(record.user_id),
            claims={"kind": self.kind, "token_id": record.pk},
        )

    def _lookup(self, token: str):
        _fresh_connection()
        try:
            return tokens.lookup(token, self.kind)
        finally:
            _fresh_connection()


@dataclass
class Principal:
    """Who a tool call acts for. Loaded fresh on every call, so a revoked token stops working at once."""

    user: object
    account: object  # None for operators
    token: AgentToken


class NotSignedIn(Exception):
    pass


async def call(kind: str, fn, **kwargs):
    """Run a service-layer function for the caller of the current MCP request.

    The access token comes from the request context (set by the SDK's auth
    middleware); the database work happens in a thread, as Django requires.
    """
    access = get_access_token()
    if access is None or (access.claims or {}).get("kind") != kind:
        raise NotSignedIn("This endpoint needs a valid Cassilion agent token.")
    return await sync_to_async(_run)(kind, access.token, fn, kwargs)


def _run(kind: str, plaintext: str, fn, kwargs):
    _fresh_connection()
    try:
        record = tokens.lookup(plaintext, kind)
        if record is None:
            raise NotSignedIn("The agent token is no longer valid.")
        return fn(Principal(user=record.user, account=record.account, token=record), **kwargs)
    finally:
        _fresh_connection()
