from .models import AuditEvent


def audit(action: str, *, actor=None, actor_kind: str | None = None, account=None, obj: str = "", data: dict | None = None) -> AuditEvent:
    """Write one audit line. Never put secrets or message bodies into `data`."""
    if actor_kind is None:
        actor_kind = "user" if actor is not None else "system"
    return AuditEvent.objects.create(
        action=action,
        actor=actor,
        actor_kind=actor_kind,
        account=account,
        object_ref=obj,
        data=data or {},
    )
