from django.contrib import admin

from .models import Artefact, Download, Release


class ArtefactInline(admin.TabularInline):
    model = Artefact
    extra = 0


@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = ["version", "channel", "is_published", "published_at"]
    list_filter = ["channel", "is_published"]
    inlines = [ArtefactInline]


@admin.register(Download)
class DownloadAdmin(admin.ModelAdmin):
    list_display = ["created_at", "account", "artefact", "via"]
    list_filter = ["via", "artefact__release"]
    readonly_fields = ["account", "user", "artefact", "via", "created_at"]
