from django.urls import path

from . import sales, views

app_name = "backoffice"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("review/", views.review_queue, name="review_queue"),
    path("tickets/", views.tickets, name="tickets"),
    path("tickets/<str:public_id>/", views.ticket, name="ticket"),
    path("sent/", views.sent_log, name="sent_log"),
    path("products/", sales.products, name="products"),
    path("products/new/", sales.product_edit, name="product_new"),
    path("products/<slug:code>/", sales.product_edit, name="product"),
    path("plans/new/", sales.plan_edit, name="plan_new"),
    path("plans/<slug:code>/", sales.plan_edit, name="plan"),
    path("orders/", sales.orders, name="orders"),
    path("orders/<str:ref>/", sales.order, name="order"),
    path("invoices/", sales.invoices, name="invoices"),
    path("invoices/export/<int:year>/<int:month>/", sales.invoice_export, name="invoice_export"),
    path("invoices/<str:number>/", sales.invoice_pdf, name="invoice_pdf"),
    path("payments/", sales.payment_issues, name="payment_issues"),
    path("payments/bank/", sales.bank_import, name="bank_import"),
    path("payments/settings/", sales.payment_settings, name="payment_settings"),
    path("payments/settings/test-orders/delete/", sales.purge_test_orders, name="purge_test_orders"),
    path("payments/settings/<slug:provider>/check/", sales.payment_check, name="payment_check"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/tokens/new/", views.operator_token_create, name="operator_token_create"),
    path("settings/tokens/<int:token_id>/revoke/", views.operator_token_revoke, name="operator_token_revoke"),
]
