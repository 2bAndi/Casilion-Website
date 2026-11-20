"""The license file format. Pure functions, no Django: the same code can be vendored into tooling.

    -----BEGIN CASSILION LICENSE-----
    <base64url(canonical JSON)>.<base64url(Ed25519 signature)>
    -----END CASSILION LICENSE-----

The database ships the public keys and checks the signature offline. The file
identifies customer and plan; it does not unlock the software.
"""
import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

HEADER = "-----BEGIN CASSILION LICENSE-----"
FOOTER = "-----END CASSILION LICENSE-----"
FORMAT_VERSION = 1


class LicenseError(Exception):
    pass


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict, private_key: Ed25519PrivateKey) -> str:
    return _b64(private_key.sign(canonical(payload)))


def encode_file(payload: dict, signature: str) -> str:
    return f"{HEADER}\n{_b64(canonical(payload))}.{signature}\n{FOOTER}\n"


def decode_file(text: str) -> tuple[dict, bytes, bytes]:
    """Return (payload, signed bytes, signature) without judging the signature."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 3 or lines[0] != HEADER or lines[-1] != FOOTER:
        raise LicenseError("Not a Cassilion license file.")
    body = "".join(lines[1:-1])
    try:
        encoded_payload, encoded_signature = body.split(".", 1)
        signed = _unb64(encoded_payload)
        return json.loads(signed), signed, _unb64(encoded_signature)
    except (ValueError, json.JSONDecodeError) as exc:
        raise LicenseError("The license file is damaged.") from exc


def verify(text: str, public_keys: dict[str, Ed25519PublicKey]) -> dict:
    """Return the payload if the signature is valid for a known key. Raise LicenseError otherwise."""
    payload, signed, signature = decode_file(text)
    key = public_keys.get(str(payload.get("key_id")))
    if key is None:
        raise LicenseError(f"Unknown signing key: {payload.get('key_id')!r}")
    try:
        key.verify(signature, signed)
    except InvalidSignature as exc:
        raise LicenseError("The signature does not match. The file was altered or not issued by Cassilion.") from exc
    return payload
