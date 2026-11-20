"""Encrypted text field for secrets stored in the database (payment keys).

Values are Fernet-encrypted with a key derived from ``FIELD_ENCRYPTION_KEY`` (falls back
to ``SECRET_KEY``). Rotating the key requires re-saving the affected rows. Stored values
carry an ``enc:`` prefix so legacy plaintext values still load.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

PREFIX = "enc:"


def _fernet() -> Fernet:
    secret = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        if value.startswith(PREFIX):
            return value
        return PREFIX + _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value or not value.startswith(PREFIX):
            return value
        try:
            return _fernet().decrypt(value[len(PREFIX) :].encode()).decode()
        except InvalidToken:
            return ""  # wrong key: behave as "not configured" rather than crash the site
