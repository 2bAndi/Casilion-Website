from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Cassilion"
admin.site.site_title = "Cassilion backoffice"
admin.site.index_title = "Raw data"

urlpatterns = [path("ops/admin/", admin.site.urls)] if settings.CASSILION["DJANGO_ADMIN"] else []
urlpatterns += [
    path("", include("apps.core.urls")),
    path("auth/", include("allauth.urls")),
    path("account/", include("apps.accounts.urls")),
    path("account/licenses/", include("apps.licensing.urls")),
    path("account/downloads/", include("apps.downloads.urls")),
    path("account/tickets/", include("apps.support.urls")),
    path("account/agents/", include("apps.mcp_server.urls")),
    path("", include("apps.billing.urls")),
    path("internal/jobs/", include("apps.support.job_api")),
    path("ops/", include("apps.backoffice.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
]
