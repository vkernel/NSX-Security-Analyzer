"""Authenticated encryption for saved manager credentials."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError


def cipher():
    # Separate the credential key from other uses of the application's random secret.
    key = hashlib.sha256(("nsx-manager-credentials:v1:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_password(password):
    return cipher().encrypt(password.encode()).decode()


def decrypt_password(token):
    try:
        return cipher().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise ValidationError("The saved password could not be decrypted. Re-enter it in Edit environment.") from exc
