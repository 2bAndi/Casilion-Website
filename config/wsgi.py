"""WSGI entry point. Serves the site without the MCP endpoints; use config.asgi for those."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
