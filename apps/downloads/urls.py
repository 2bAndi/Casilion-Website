from django.urls import path

from . import views

app_name = "downloads"

urlpatterns = [
    path("", views.downloads, name="downloads"),
    path("link/<int:artefact_id>/", views.request_link, name="request_link"),
    path("file/<str:token>/", views.file, name="file"),
]
