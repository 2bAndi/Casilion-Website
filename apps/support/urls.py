from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("", views.ticket_list, name="list"),
    path("new/", views.ticket_new, name="new"),
    path("<str:public_id>/", views.ticket_detail, name="detail"),
    path("<str:public_id>/person/", views.ticket_human, name="human"),
    path("<str:public_id>/close/", views.ticket_close, name="close"),
]
