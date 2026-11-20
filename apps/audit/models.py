from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Append-only record of who did what, and on whose behalf."""

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=80, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_kind = models.CharField(max_length=20, default="user")  # user | agent | operator | ai | system
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    object_ref = models.CharField(max_length=120, blank=True)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-at", "-id"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action}"
