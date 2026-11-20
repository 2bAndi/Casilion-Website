from django.contrib import admin

from .models import Order, PaymentEvent, Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "price_eur_year", "support_level", "ticket_quota_month", "self_service", "is_public", "rank"]
    list_editable = ["rank"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["account", "plan", "status", "period_end", "provider", "created_at"]
    list_filter = ["status", "plan", "provider"]
    search_fields = ["account__name", "provider_ref"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["ref", "account", "plan", "amount_eur", "status", "created_at", "paid_at"]
    list_filter = ["status", "plan"]
    search_fields = ["ref", "account__name"]


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ["received_at", "provider", "type", "event_id", "processed_at", "error"]
    list_filter = ["provider", "type"]
    readonly_fields = [f.name for f in PaymentEvent._meta.fields]
