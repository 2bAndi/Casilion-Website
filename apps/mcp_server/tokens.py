import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from apps.audit.log import audit

from .models import AgentToken

_PREFIX = {AgentToken.Kind.CUSTOMER: "cas_agent_", AgentToken.Kind.OPERATOR: "cas_ops_"}


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def issue(user, *, kind: str, account=None, name: str = "", days_valid: int | None = 365) -> tuple[AgentToken, str]:
    """Create a token and return it with its plaintext. The plaintext cannot be recovered later."""
    plaintext = _PREFIX[kind] + secrets.token_urlsafe(32)
    record = AgentToken.objects.create(
        kind=kind,
        user=user,
        account=account,
        name=name.strip()[:80],
        prefix=plaintext[:14],
        token_hash=_hash(plaintext),
        expires_at=timezone.now() + timedelta(days=days_valid) if days_valid else None,
    )
    audit("agent_token.created", actor=user, account=account, obj=record.prefix, data={"kind": kind})
    return record, plaintext


def revoke(record: AgentToken, user) -> None:
    if record.revoked_at is None:
        record.revoked_at = timezone.now()
        record.save(update_fields=["revoked_at"])
        audit("agent_token.revoked", actor=user, account=record.account, obj=record.prefix)


def lookup(plaintext: str, kind: str) -> AgentToken | None:
    """The token record if the token is valid for this kind of endpoint right now."""
    if not plaintext or not plaintext.startswith(_PREFIX[kind]):
        return None
    record = (
        AgentToken.objects.select_related("user", "account")
        .filter(token_hash=_hash(plaintext), kind=kind, revoked_at__isnull=True)
        .first()
    )
    now = timezone.now()
    if record is None or not record.user.is_active or (record.expires_at and record.expires_at < now):
        return None
    if kind == AgentToken.Kind.OPERATOR and not record.user.is_staff:
        return None
    if kind == AgentToken.Kind.CUSTOMER and (record.account is None or record.account.status != "active"):
        return None
    if record.last_used_at is None or now - record.last_used_at > timedelta(minutes=1):
        AgentToken.objects.filter(pk=record.pk).update(last_used_at=now)
    return record
