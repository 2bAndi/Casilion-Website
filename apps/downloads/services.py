import hashlib
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.urls import reverse

from .models import Artefact, Release

_SALT = "cassilion.download"


def published_releases():
    return Release.objects.filter(is_published=True).prefetch_related("artefacts")


def make_link(artefact: Artefact, account, user=None, via: str = "web") -> str:
    """A short-lived link bound to one account and one artefact."""
    token = signing.dumps(
        {"a": artefact.pk, "acc": str(account.pk), "u": user.pk if user else None, "via": via}, salt=_SALT
    )
    return settings.CASSILION["SITE_URL"] + reverse("downloads:file", args=[token])


def read_link(token: str) -> dict:
    return signing.loads(token, salt=_SALT, max_age=settings.CASSILION["DOWNLOAD_LINK_SECONDS"])


def artefact_path(artefact: Artefact) -> Path:
    root = Path(settings.CASSILION["RELEASES_DIR"]).resolve()
    path = (root / artefact.storage_path).resolve()
    if root not in path.parents and path != root:
        raise FileNotFoundError("Artefact path escapes the releases directory.")
    return path


def register_artefact(release: Release, platform: str, source: Path) -> Artefact:
    """Record a file that already lies below RELEASES_DIR (what CI does after publishing a build)."""
    root = Path(settings.CASSILION["RELEASES_DIR"]).resolve()
    data = source.read_bytes()
    artefact, _ = Artefact.objects.update_or_create(
        release=release,
        platform=platform,
        defaults={
            "filename": source.name,
            "storage_path": str(source.resolve().relative_to(root)).replace("\\", "/"),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    return artefact
