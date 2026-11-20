from django.urls import path

from . import views

app_name = "licensing"

urlpatterns = [
    path("", views.licenses, name="licenses"),
    path("public-key.pem", views.public_key, name="public_key"),
    path("<str:license_id>/cassilion.lic", views.download, name="download"),
]
