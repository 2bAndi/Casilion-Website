from django.contrib import admin

from .models import ContractNotice


@admin.register(ContractNotice)
class ContractNoticeAdmin(admin.ModelAdmin):
    list_display = ["reference", "kind", "name", "email", "contract", "received_at", "handled_at"]
    list_filter = ["kind", "handled_at"]
    search_fields = ["reference", "name", "email", "contract"]
    # The declaration is evidence: it stays as it arrived. Only the handling fields are open.
    readonly_fields = [
        "reference", "kind", "name", "email", "contract", "cancel_type", "cancel_reason", "cancel_at",
        "language", "received_at", "user", "confirmation_sent",
    ]
    exclude = ["token"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
