from django.contrib import admin

from .models import AIJob, AIRun, Finding, GuardVerdict, Message, Review, Ticket, TicketEvent


class MessageInline(admin.StackedInline):
    model = Message
    extra = 0
    fields = ["author_kind", "visibility", "state", "body", "ai_generated", "sent_at"]
    readonly_fields = fields
    can_delete = False


class EventInline(admin.TabularInline):
    model = TicketEvent
    extra = 0
    fields = ["at", "actor_kind", "actor", "type", "data"]
    readonly_fields = fields
    can_delete = False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ["public_id", "subject", "account", "status", "priority", "classification", "channel", "created_at"]
    list_filter = ["status", "priority", "classification", "channel", "bug_report", "flagged"]
    search_fields = ["public_id", "subject", "account__name"]
    inlines = [MessageInline, EventInline]


@admin.register(AIJob)
class AIJobAdmin(admin.ModelAdmin):
    list_display = ["id", "ticket", "status", "attempts", "worker_id", "created_at", "finished_at", "error"]
    list_filter = ["status"]


class FindingInline(admin.StackedInline):
    model = Finding
    extra = 0


class VerdictInline(admin.TabularInline):
    model = GuardVerdict
    extra = 0


@admin.register(AIRun)
class AIRunAdmin(admin.ModelAdmin):
    list_display = ["id", "ticket", "backend", "model", "tokens_in", "tokens_out", "duration_ms", "created_at"]
    inlines = [FindingInline, VerdictInline]


admin.site.register(Review)
