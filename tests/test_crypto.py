import base64

import pytest
from cryptography.fernet import InvalidToken

from chatcli import crypto, paths


def test_derive_key_is_deterministic_and_fernet_shaped():
    key = crypto.derive_key("pw", b"s" * 16)
    assert key == crypto.derive_key("pw", b"s" * 16)
    assert len(base64.urlsafe_b64decode(key)) == 32


def test_derive_key_depends_on_password_and_salt():
    base = crypto.derive_key("pw", b"s" * 16)
    assert crypto.derive_key("other", b"s" * 16) != base
    assert crypto.derive_key("pw", b"t" * 16) != base


def test_salt_is_created_once_and_reused():
    first = crypto.get_or_create_salt()
    assert len(first) == 16
    assert crypto.get_or_create_salt() == first
    assert paths.SALT_FILE.read_bytes() == first
    assert not list(paths.APP_DIR.glob("*.tmp"))


def test_make_fernet_round_trips_and_rejects_a_different_password():
    token = crypto.make_fernet("right").encrypt(b"secret")
    assert crypto.make_fernet("right").decrypt(token) == b"secret"
    with pytest.raises(InvalidToken):
        crypto.make_fernet("wrong").decrypt(token)
