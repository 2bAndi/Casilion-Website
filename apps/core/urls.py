from django.urls import path

from . import legal, views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("docs/", views.docs_index, name="docs"),
    path("docs/<slug:slug>/", views.doc, name="doc"),
    # The two functions come before the texts, or "withdraw" would be read as the name of a text.
    path("legal/withdraw/", legal.withdraw, name="withdraw"),
    path("legal/cancel/", legal.cancel_contract, name="cancel_contract"),
    path("legal/receipt/<str:token>/", legal.notice_receipt, name="notice_receipt"),
    path("legal/<slug:slug>/", legal.page, name="legal"),
    path("healthz", views.healthz, name="healthz"),
    path("robots.txt", views.robots_txt),
    path("favicon.ico", views.favicon),
]
