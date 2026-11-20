from django.contrib import admin

from .models import Entitlement, License


class LicenseInline(admin.TabularInline):
    model = License
    extra = 0
    fields = ["license_id", "key_id", "issued_at", "revoked_at"]
    readonly_fields = fields
    can_delete = False


@admin.register(Entitlement)
class EntitlementAdmin(admin.ModelAdmin):
    list_display = ["account", "product", "plan", "source", "valid_from", "valid_until", "is_active"]
    list_filter = ["product", "plan", "source", "is_active"]
    search_fields = ["account__name"]
    inlines = [LicenseInline]


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):
    list_display = ["license_id", "entitlement", "key_id", "issued_at", "revoked_at"]
    search_fields = ["license_id", "entitlement__account__name"]
    readonly_fields = ["license_id", "entitlement", "key_id", "payload", "signature", "issued_at"]
