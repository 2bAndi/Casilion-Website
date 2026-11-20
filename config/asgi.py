"""ASGI entry point: Django for the site, plus the two MCP endpoints.

    /mcp       customer MCP (a customer's AI agent)
    /ops/mcp   operator MCP (the Cassilion team)
    the rest   Django
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

django_application = get_asgi_application()

from apps.mcp_server.asgi import build_application  # noqa: E402

application = build_application(django_application)
