from django.contrib import admin

from .models import Order, OrderLine, Payment, PaymentEvent, Plan, Product, ReconciliationIssue, Refund, Subscription


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "license_prefix", "is_public", "rank"]
    list_editable = ["rank"]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "product", "code", "price_eur_year", "support_level", "ticket_quota_month", "self_service", "is_public", "rank"]
    list_filter = ["product"]
    list_editable = ["rank"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["account", "plan", "status", "period_end", "provider", "created_at"]
    list_filter = ["status", "plan", "provider"]
    search_fields = ["account__name", "provider_ref"]


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["ref", "account", "kind", "total", "status", "payment_method", "created_at", "paid_at"]
    list_filter = ["status", "kind", "payment_method"]
    search_fields = ["ref", "account__name"]
    inlines = [OrderLineInline]


class _ReadOnly(admin.ModelAdmin):
    """Money records are written by the application only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(_ReadOnly):
    list_display = ["created_at", "order", "provider", "provider_id", "amount", "status"]
    list_filter = ["provider", "status"]
    search_fields = ["provider_id", "provider_ref", "order__ref"]


@admin.register(Refund)
class RefundAdmin(_ReadOnly):
    list_display = ["created_at", "order", "amount", "reason", "created_by"]


@admin.register(PaymentEvent)
class PaymentEventAdmin(_ReadOnly):
    list_display = ["created_at", "source", "kind", "provider", "provider_type", "order", "amount", "outcome"]
    list_filter = ["source", "provider", "kind"]


@admin.register(ReconciliationIssue)
class ReconciliationIssueAdmin(admin.ModelAdmin):
    list_display = ["created_at", "kind", "provider", "provider_id", "detail", "resolved"]
    list_filter = ["kind", "resolved"]
