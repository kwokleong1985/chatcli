"""Persistence: the encrypted config file and saved conversation history."""

import json
import base64
import datetime
from pathlib import Path
from typing import List

from cryptography.fernet import Fernet, InvalidToken

from .fileio import atomic_write_bytes, atomic_write_text
from .models import CONFIG_VERSION, Config, Session
from .paths import CONFIG_FILE, HISTORY_DIR, SALT_FILE


class WrongPasswordError(Exception):
    """The config could not be decrypted: wrong master password or a corrupted file."""


class UnsupportedConfigError(Exception):
    """The config was written by a newer ChatCLI. Loading it could lose data on save."""


class ExportError(Exception):
    """The file being imported wasn't produced by ChatCLI's config export."""


EXPORT_VERSION = 1


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


# ── Portable export/import ──────────────────────────────────────────────────

def export_config(dest: Path) -> None:
    """Bundle the encrypted config and its salt into one file.

    The bundle is still encrypted with the master password; copy it to another
    machine and use `import_config` there with the same password to unlock it.
    """
    if not CONFIG_FILE.exists():
        raise FileNotFoundError("Nothing saved yet: add an endpoint, prompt or MCP server first.")
    payload = {
        "chatcli_export": EXPORT_VERSION,
        "salt": base64.b64encode(SALT_FILE.read_bytes()).decode(),
        "config": base64.b64encode(CONFIG_FILE.read_bytes()).decode(),
    }
    atomic_write_text(dest, json.dumps(payload))


def import_config(src: Path) -> None:
    """Restore a file written by `export_config`, replacing the local salt and
    encrypted config. Takes effect on the next unlock with that export's master
    password; the currently running session keeps using the config it already
    loaded.
    """
    try:
        data = json.loads(Path(src).read_text(encoding="utf-8"))
        salt = base64.b64decode(data["salt"])
        blob = base64.b64decode(data["config"])
    except (OSError, ValueError, KeyError) as exc:
        raise ExportError(f"Not a chatcli export file: {exc}") from None
    if data.get("chatcli_export") != EXPORT_VERSION:
        raise ExportError("Export was made by an incompatible version of ChatCLI.")
    # Config first: if this fails, the old salt+config pair is left intact and
    # still decryptable together. If salt were written first and this then
    # failed, the old config would be stranded under a salt that can't open it.
    atomic_write_bytes(CONFIG_FILE, blob)
    atomic_write_bytes(SALT_FILE, salt)


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
