from django.db import models

from shared.schemas import ANSWER_CLASSES


def _default_classes():
    return list(ANSWER_CLASSES)


class SupportSettings(models.Model):
    """The operators' switches. One row.

    Automatic sending starts switched off (shadow mode): every AI-written reply
    waits for a person. Fixed templates (acknowledgement, request for information)
    are sent either way, because they contain nothing the AI wrote.
    """

    auto_send = models.BooleanField(default=False, help_text="Kill switch. Off = every AI-written reply is reviewed.")
    auto_send_classes = models.JSONField(default=_default_classes, help_text="Classes that may be sent automatically.")
    max_rounds = models.PositiveSmallIntegerField(default=6, help_text="Customer follow-ups before a person takes over.")
    max_tokens_per_ticket = models.PositiveIntegerField(default=200_000)
    stale_days = models.PositiveSmallIntegerField(default=14, help_text="Days without customer reply before a ticket closes.")
    worker_last_seen = models.DateTimeField(null=True, blank=True)
    worker_last_id = models.CharField(max_length=80, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "Support settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "SupportSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Support settings"
