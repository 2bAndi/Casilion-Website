"""Mounts the two MCP servers next to Django in one ASGI application."""
from contextlib import AsyncExitStack

from django.conf import settings
from mcp.server.transport_security import TransportSecuritySettings

from . import customer, operator

CUSTOMER_PATH = "/mcp"
OPERATOR_PATH = "/ops/mcp"
_METADATA = "/.well-known/oauth-protected-resource"


def build_application(django_application):
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.CASSILION["MCP_ALLOWED_HOSTS"],
        allowed_origins=settings.CASSILION["MCP_ALLOWED_ORIGINS"],
    )
    customer_server, operator_server = customer.build(), operator.build()
    # Stateless requests, JSON responses: each call stands alone, as the current MCP revision intends.
    customer_app = customer_server.streamable_http_app(
        streamable_http_path=CUSTOMER_PATH, stateless_http=True, json_response=True, transport_security=security
    )
    operator_app = operator_server.streamable_http_app(
        streamable_http_path=OPERATOR_PATH, stateless_http=True, json_response=True, transport_security=security
    )
    managers = [customer_server.session_manager, operator_server.session_manager]

    async def application(scope, receive, send):
        if scope["type"] == "lifespan":
            await _lifespan(managers, receive, send)
            return
        path = scope.get("path", "")
        if path == OPERATOR_PATH or path == _METADATA + OPERATOR_PATH:
            await operator_app(scope, receive, send)
        elif path == CUSTOMER_PATH or path == _METADATA + CUSTOMER_PATH:
            await customer_app(scope, receive, send)
        else:
            await django_application(scope, receive, send)

    return application


async def _lifespan(managers, receive, send):
    """Run the MCP session managers for as long as the server lives."""
    async with AsyncExitStack() as stack:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    for manager in managers:
                        await stack.enter_async_context(manager.run())
                except Exception as exc:  # pragma: no cover - startup failure is reported to the server
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    raise
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await stack.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return
