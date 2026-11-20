from django.contrib import admin

from .models import Conversation, MailLog


class _ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MailLog)
class MailLogAdmin(_ReadOnly):
    list_display = ["created_at", "to", "subject", "status", "account"]
    list_filter = ["status"]
    search_fields = ["to", "subject"]


@admin.register(Conversation)
class ConversationAdmin(_ReadOnly):
    list_display = ["last_message_at", "contact_email", "subject", "status", "account"]
    list_filter = ["status", "is_automatic"]
    search_fields = ["contact_email", "subject"]
