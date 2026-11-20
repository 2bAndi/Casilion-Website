import secrets

from django.db import transaction
from django.utils import timezone

from apps.audit.log import audit

from . import codec, keys
from .models import Entitlement, License

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I


def new_license_id(prefix: str = "CAS") -> str:
    groups = ["".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3)]
    return f"{prefix or 'CAS'}-" + "-".join(groups)


def default_product():
    from apps.billing.models import Product

    return Product.default()


def community_plan():
    """The free plan of the default product: what every account has from registration on."""
    return default_product().free_plan()


@transaction.atomic
def grant(account, plan, *, source: str, valid_until=None) -> Entitlement:
    """Give the account this plan from today, replacing its current plan of the same product, and issue the license file."""
    product = plan.product
    account.entitlements.filter(is_active=True, product=product).update(is_active=False)
    License.objects.filter(entitlement__account=account, entitlement__product=product, revoked_at__isnull=True).update(revoked_at=timezone.now())
    entitlement = Entitlement.objects.create(
        account=account, product=product, plan=plan, valid_from=timezone.localdate(), valid_until=valid_until, source=source
    )
    issue_license(entitlement)
    audit("license.granted", account=account, obj=str(entitlement.pk), data={"product": product.code, "plan": plan.code, "source": source})
    return entitlement


def grant_free(account, product) -> Entitlement | None:
    """Back to the product's free plan, if it has one."""
    plan = product.free_plan()
    return grant(account, plan, source=Entitlement.Source.FREE) if plan else None


def grant_community(account) -> Entitlement:
    return grant(account, community_plan(), source=Entitlement.Source.FREE)


def current_entitlement(account, product=None) -> Entitlement | None:
    """The entitlement in force today for a product (default: the main product).

    An expired paid plan falls back to the product's free plan on the spot. For the main product
    there is always one; for the others ``None`` means the account never took a license.
    """
    today = timezone.localdate()
    main = product is None
    product = product or default_product()
    entitlement = account.entitlements.select_related("plan", "product").filter(is_active=True, product=product).first()
    if entitlement and (entitlement.valid_until is None or entitlement.valid_until >= today):
        return entitlement
    if entitlement is None and not main:
        return None
    return grant_free(account, product)


def entitlements(account) -> list[Entitlement]:
    """Everything the account holds today, one entry per product."""
    from apps.billing.models import Product

    found = [current_entitlement(account)]
    for product in Product.objects.exclude(pk=found[0].product_id):
        entitlement = current_entitlement(account, product)
        if entitlement:
            found.append(entitlement)
    return found


def issue_license(entitlement: Entitlement) -> License:
    account, plan = entitlement.account, entitlement.plan
    license_id = new_license_id(entitlement.product.license_prefix)
    payload = {
        "v": codec.FORMAT_VERSION,
        "key_id": keys.key_id(),
        "license_id": license_id,
        "account": str(account.pk),
        "licensee": account.name,
        "product": entitlement.product.code,
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


def license_filename(license: License) -> str:
    return f"{license.payload.get('product') or 'cassilion'}.lic"


def license_file(license: License) -> str:
    return codec.encode_file(license.payload, license.signature)


def active_license(account, product=None) -> License | None:
    entitlement = current_entitlement(account, product)
    return entitlement.licenses.filter(revoked_at__isnull=True).first() if entitlement else None
