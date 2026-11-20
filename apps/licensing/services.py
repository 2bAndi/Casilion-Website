import secrets

from django.db import transaction
from django.utils import timezone

from apps.audit.log import audit

from . import codec, keys
from .models import Entitlement, License

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I


def new_license_id() -> str:
    groups = ["".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "CAS-" + "-".join(groups)


def community_plan():
    from apps.billing.models import Plan

    return Plan.objects.get(code="community")


@transaction.atomic
def grant(account, plan, *, source: str, valid_until=None) -> Entitlement:
    """Give the account this plan from today, replacing its current entitlement, and issue the license file."""
    account.entitlements.filter(is_active=True).update(is_active=False)
    License.objects.filter(entitlement__account=account, revoked_at__isnull=True).update(revoked_at=timezone.now())
    entitlement = Entitlement.objects.create(
        account=account, plan=plan, valid_from=timezone.localdate(), valid_until=valid_until, source=source
    )
    issue_license(entitlement)
    audit("license.granted", account=account, obj=str(entitlement.pk), data={"plan": plan.code, "source": source})
    return entitlement


def grant_community(account) -> Entitlement:
    return grant(account, community_plan(), source=Entitlement.Source.FREE)


def current_entitlement(account) -> Entitlement:
    """The entitlement in force today. An expired paid plan falls back to Community on the spot."""
    today = timezone.localdate()
    entitlement = account.entitlements.select_related("plan").filter(is_active=True).first()
    if entitlement and (entitlement.valid_until is None or entitlement.valid_until >= today):
        return entitlement
    return grant_community(account)


def issue_license(entitlement: Entitlement) -> License:
    account, plan = entitlement.account, entitlement.plan
    license_id = new_license_id()
    payload = {
        "v": codec.FORMAT_VERSION,
        "key_id": keys.key_id(),
        "license_id": license_id,
        "account": str(account.pk),
        "licensee": account.name,
        "plan": plan.code,
        "support": plan.support_level,
        "valid_from": entitlement.valid_from.isoformat(),
        "valid_until": entitlement.valid_until.isoformat() if entitlement.valid_until else None,
        "issued_at": timezone.now().replace(microsecond=0).isoformat(),
    }
    return License.objects.create(
        license_id=license_id,
        entitlement=entitlement,
        key_id=payload["key_id"],
        payload=payload,
        signature=codec.sign(payload, keys.private_key()),
    )


def license_file(license: License) -> str:
    return codec.encode_file(license.payload, license.signature)


def active_license(account) -> License | None:
    entitlement = current_entitlement(account)
    return entitlement.licenses.filter(revoked_at__isnull=True).first()
