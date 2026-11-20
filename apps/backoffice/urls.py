from django.urls import path

from . import views

app_name = "backoffice"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("review/", views.review_queue, name="review_queue"),
    path("tickets/", views.tickets, name="tickets"),
    path("tickets/<str:public_id>/", views.ticket, name="ticket"),
    path("sent/", views.sent_log, name="sent_log"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/tokens/new/", views.operator_token_create, name="operator_token_create"),
    path("settings/tokens/<int:token_id>/revoke/", views.operator_token_revoke, name="operator_token_revoke"),
]
