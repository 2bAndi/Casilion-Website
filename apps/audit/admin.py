from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["at", "action", "actor_kind", "actor", "account", "object_ref"]
    list_filter = ["action", "actor_kind"]
    search_fields = ["action", "object_ref", "actor__email", "account__name"]
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
