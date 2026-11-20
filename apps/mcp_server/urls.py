from django.urls import path

from . import views

app_name = "mcp_server"

urlpatterns = [
    path("", views.agents, name="agents"),
    path("new/", views.create, name="create"),
    path("<int:token_id>/revoke/", views.revoke, name="revoke"),
]
