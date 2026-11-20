from django.conf import settings
from django.db import connection
from django.http import JsonResponse


class HealthzMiddleware:
    """Answer /healthz before the host check: Docker and a watchdog call it as
    127.0.0.1, which is (rightly) not an allowed host of the site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz":
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            except Exception:
                return JsonResponse({"ok": False, "status": "database unavailable"}, status=503)
            return JsonResponse({"ok": True})
        return self.get_response(request)


class RobotsMiddleware:
    """While SITE_NOINDEX is on, every response tells search engines to stay out."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if settings.SITE_NOINDEX:
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
