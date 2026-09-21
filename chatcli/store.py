"""Persistence: the encrypted config file and saved conversation history."""

import json
import datetime
from typing import List

from cryptography.fernet import Fernet, InvalidToken

from .fileio import atomic_write_bytes, atomic_write_text
from .models import CONFIG_VERSION, Config, Session
from .paths import CONFIG_FILE, HISTORY_DIR


class WrongPasswordError(Exception):
    """The config could not be decrypted: wrong master password or a corrupted file."""


class UnsupportedConfigError(Exception):
    """The config was written by a newer ChatCLI. Loading it could lose data on save."""


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(fernet: Fernet) -> Config:
    if not CONFIG_FILE.exists():
        return Config()
    try:
        data = json.loads(fernet.decrypt(CONFIG_FILE.read_bytes()))
    except InvalidToken:
        raise WrongPasswordError from None
    # Configs saved before "version" existed count as 0. Sections they lack (and any
    # fields added since) are filled by the model defaults, so no per-version
    # migration code is needed yet; add it here when a change can't be defaulted.
    version = data.get("version", 0)
    if version > CONFIG_VERSION:
        raise UnsupportedConfigError(version)
    return Config.from_dict(data)


def save_config(config: Config, fernet: Fernet) -> None:
    atomic_write_bytes(CONFIG_FILE, fernet.encrypt(json.dumps(config.to_dict()).encode()))


# ── Conversation history ──────────────────────────────────────────────────────

def list_sessions() -> List[Session]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for f in sorted(HISTORY_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            sess = Session.from_dict(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
        sess.path = f
        sess.updated = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        sessions.append(sess)
    return sessions


def save_session(sess: Session) -> None:
    # to_dict leaves out api_key: it lives in the encrypted config and is re-read
    # from there on resume (see resume_session).
    atomic_write_text(sess.path, json.dumps(sess.to_dict(), indent=2, ensure_ascii=False))
