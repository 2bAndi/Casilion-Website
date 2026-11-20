from django.contrib import admin

from .models import AgentToken


@admin.register(AgentToken)
class AgentTokenAdmin(admin.ModelAdmin):
    list_display = ["prefix", "kind", "user", "account", "name", "created_at", "last_used_at", "expires_at", "revoked_at"]
    list_filter = ["kind"]
    search_fields = ["prefix", "user__email", "account__name"]
    readonly_fields = ["kind", "user", "account", "prefix", "token_hash", "created_at", "last_used_at"]

    def has_add_permission(self, request):
        return False
