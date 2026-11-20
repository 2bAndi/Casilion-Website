from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("pricing/", views.pricing, name="pricing"),
    path("account/billing/", views.overview, name="overview"),
    path("account/billing/cancel/", views.cancel, name="cancel"),
    path("billing/checkout/<slug:code>/", views.checkout_start, name="checkout_start"),
    path("billing/sandbox/<str:ref>/", views.sandbox_checkout, name="sandbox_checkout"),
    path("billing/webhook/<slug:provider>/", views.webhook, name="webhook"),
]
