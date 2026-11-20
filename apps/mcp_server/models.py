from django.conf import settings
from django.db import models


class AgentToken(models.Model):
    """An access token for an AI agent. Only its hash is stored; the token itself is shown once.

    Skeleton stage: tokens are created in the customer area after login (with the
    second factor, when REQUIRE_MFA_FOR_AGENT_TOKENS is on). The next step is the
    OAuth 2.1 flow of the MCP specification with this site as authorization server;
    the MCP layer already checks tokens through one verifier, so only issuing changes.
    """

    class Kind(models.TextChoices):
        CUSTOMER = "customer", "Customer agent"
        OPERATOR = "operator", "Operator agent"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_tokens")
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.CASCADE, related_name="agent_tokens")
    name = models.CharField(max_length=80, blank=True)
    prefix = models.CharField(max_length=16)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.prefix}… ({self.name or self.kind})"
