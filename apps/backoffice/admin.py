from django.contrib import admin

from .models import SupportSettings

admin.site.site_header = "Cassilion data"
admin.site.site_title = "Cassilion"
admin.site.index_title = "Raw data"


@admin.register(SupportSettings)
class SupportSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SupportSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
