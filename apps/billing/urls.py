from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("pricing/", views.pricing, name="pricing"),
    path("account/billing/", views.overview, name="overview"),
    path("account/billing/cancel/", views.cancel, name="cancel"),
    path("account/billing/orders/<str:ref>/", views.order_detail, name="order"),
    path("account/billing/orders/<str:ref>/pay/", views.order_pay, name="order_pay"),
    path("account/billing/invoices/<str:number>/", views.invoice, name="invoice"),
    path("billing/checkout/<slug:code>/", views.checkout_start, name="checkout_start"),
    path("billing/free/<slug:code>/", views.free_plan, name="free_plan"),
    path("billing/return/<int:payment_id>/", views.provider_return, name="return"),
    path("billing/sandbox/<str:ref>/", views.sandbox_checkout, name="sandbox_checkout"),
    path("billing/webhook/<slug:provider>/", views.webhook, name="webhook"),
]
