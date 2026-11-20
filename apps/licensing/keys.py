"""The Ed25519 signing key. It lives outside the repository, in LICENSE_KEY_DIR.

In development the key is created on first use. In production the directory is
mounted from a secret store and LICENSE_AUTOCREATE_KEY stays off.
"""
import os
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def key_id() -> str:
    return settings.CASSILION["LICENSE_KEY_ID"]


def _path() -> Path:
    return Path(settings.CASSILION["LICENSE_KEY_DIR"]) / f"{key_id()}.pem"


@lru_cache(maxsize=4)
def _load(path: str) -> Ed25519PrivateKey:
    file = Path(path)
    if file.exists():
        key = serialization.load_pem_private_key(file.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ImproperlyConfigured(f"{file} is not an Ed25519 private key.")
        return key
    if not settings.CASSILION["LICENSE_AUTOCREATE_KEY"]:
        raise ImproperlyConfigured(f"License signing key not found: {file}")
    key = Ed25519PrivateKey.generate()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    try:
        os.chmod(file, 0o600)
    except OSError:
        pass
    return key


def private_key() -> Ed25519PrivateKey:
    return _load(str(_path()))


def public_key() -> Ed25519PublicKey:
    return private_key().public_key()


def public_key_pem() -> str:
    return public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def public_keys() -> dict[str, Ed25519PublicKey]:
    """Key id -> public key. One key today; rotation adds entries here."""
    return {key_id(): public_key()}
