from django.conf import settings
from django.db import models


class Release(models.Model):
    class Channel(models.TextChoices):
        STABLE = "stable", "Stable"
        BETA = "beta", "Beta"

    version = models.CharField(max_length=40, unique=True)
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.STABLE)
    notes = models.TextField(blank=True, help_text="Public release notes (plain text).")
    published_at = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published_at", "-id"]

    def __str__(self):
        return self.version


class Artefact(models.Model):
    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="artefacts")
    platform = models.CharField(max_length=40, help_text="For example linux-x86_64, windows-x86_64.")
    filename = models.CharField(max_length=200)
    storage_path = models.CharField(max_length=300, help_text="Path below RELEASES_DIR.")
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["platform"]
        constraints = [models.UniqueConstraint(fields=["release", "platform"], name="unique_release_platform")]

    def __str__(self):
        return f"{self.release} {self.platform}"


class Download(models.Model):
    """Who fetched which build. Support uses it; it is also the list for security notices about a release."""

    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="downloads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    artefact = models.ForeignKey(Artefact, on_delete=models.CASCADE, related_name="downloads")
    via = models.CharField(max_length=10, default="web")  # web | mcp
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
