from django.db import transaction

from apps.audit.log import audit

from .models import Account, Address, Membership, TaxProfile


@transaction.atomic
def create_account(user, *, kind: str, name: str, country: str) -> Account:
    """Create the customer account for a new login and give it the free Community license."""
    from apps.licensing.services import grant_community

    account = Account.objects.create(kind=kind, name=name.strip() or user.get_full_name(), country=country)
    Membership.objects.create(user=user, account=account, role=Membership.Role.OWNER)
    Address.objects.create(account=account)
    TaxProfile.objects.create(account=account)
    grant_community(account)
    audit("account.created", actor=user, account=account, data={"kind": kind, "country": str(country)})
    return account


def account_for(user) -> Account | None:
    """The account a login acts for. One account per login for now; the model allows more."""
    if not getattr(user, "is_authenticated", False):
        return None
    membership = user.memberships.select_related("account").first()
    return membership.account if membership else None
