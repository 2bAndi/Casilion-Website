from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Account, Address, Membership, TaxProfile, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["email"]
    list_display = ["email", "full_name", "is_staff", "is_active", "date_joined"]
    search_fields = ["email", "full_name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Person", {"fields": ("full_name", "locale")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "is_staff")}),)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


class AddressInline(admin.StackedInline):
    model = Address
    extra = 0


class TaxProfileInline(admin.StackedInline):
    model = TaxProfile
    extra = 0


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "country", "status", "created_at"]
    list_filter = ["kind", "status", "country"]
    search_fields = ["name", "members__email"]
    inlines = [MembershipInline, AddressInline, TaxProfileInline]
