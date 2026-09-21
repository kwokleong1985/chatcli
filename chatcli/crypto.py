"""Master-password key derivation and the Fernet cipher for the encrypted config."""

import os
import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .fileio import atomic_write_bytes
from .paths import SALT_FILE


def get_or_create_salt() -> bytes:
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = os.urandom(16)
    atomic_write_bytes(SALT_FILE, salt)  # a torn salt would make the config undecryptable
    return salt


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def make_fernet(password: str) -> Fernet:
    return Fernet(derive_key(password, get_or_create_salt()))
