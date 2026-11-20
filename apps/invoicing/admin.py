from django.contrib import admin

from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Read only: invoices are never changed or deleted."""

    list_display = ["number", "kind", "purpose", "issue_date", "account", "order", "gross_total"]
    list_filter = ["kind", "purpose", "issue_date"]
    search_fields = ["number", "account__name", "order__ref"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
