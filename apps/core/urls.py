from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("docs/", views.docs_index, name="docs"),
    path("docs/<slug:slug>/", views.doc, name="doc"),
    path("legal/<slug:slug>/", views.legal, name="legal"),
    path("healthz", views.healthz, name="healthz"),
    path("robots.txt", views.robots_txt),
    path("favicon.ico", views.favicon),
]
