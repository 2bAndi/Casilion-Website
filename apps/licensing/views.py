from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.accounts.decorators import account_required
from apps.audit.log import audit

from . import keys, services
from .models import License


@account_required
def licenses(request):
    account = request.account
    entitlement = services.current_entitlement(account)
    return render(
        request,
        "licensing/licenses.html",
        {
            "entitlement": entitlement,
            "license": entitlement.licenses.filter(revoked_at__isnull=True).first(),
            "history": License.objects.filter(entitlement__account=account).select_related("entitlement__plan")[:20],
        },
    )


@account_required
def download(request, license_id):
    license = get_object_or_404(License, license_id=license_id, entitlement__account=request.account)
    audit("license.downloaded", actor=request.user, account=request.account, obj=license.license_id)
    response = HttpResponse(services.license_file(license), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="cassilion.lic"'
    return response


def public_key(request):
    """The public key the database uses to check license files. Safe to publish."""
    return HttpResponse(keys.public_key_pem(), content_type="text/plain; charset=utf-8")
