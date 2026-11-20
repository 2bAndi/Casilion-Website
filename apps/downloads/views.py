from django.core import signing
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import account_required
from apps.accounts.models import Account
from apps.audit.log import audit

from . import services
from .models import Artefact, Download


@account_required
def downloads(request):
    return render(
        request,
        "downloads/downloads.html",
        {"releases": services.published_releases(), "recent": request.account.downloads.select_related("artefact__release")[:8]},
    )


@account_required
@require_POST
def request_link(request, artefact_id):
    artefact = get_object_or_404(Artefact, pk=artefact_id, release__is_published=True)
    return redirect(services.make_link(artefact, request.account, request.user))


def file(request, token):
    """Serve a build for a valid, unexpired link. The link itself is the credential, so agents can use it too."""
    try:
        data = services.read_link(token)
    except signing.SignatureExpired:
        return HttpResponseForbidden("This download link has expired. Request a new one in your account.")
    except signing.BadSignature:
        raise Http404 from None
    artefact = get_object_or_404(Artefact, pk=data["a"], release__is_published=True)
    account = get_object_or_404(Account, pk=data["acc"])
    try:
        path = services.artefact_path(artefact)
        handle = path.open("rb")
    except OSError:
        raise Http404("The file for this build is not available.") from None
    Download.objects.create(account=account, user_id=data.get("u"), artefact=artefact, via=data.get("via", "web"))
    audit("download.served", account=account, obj=f"{artefact.release.version}/{artefact.platform}", data={"via": data.get("via", "web")})
    return FileResponse(handle, as_attachment=True, filename=artefact.filename)
