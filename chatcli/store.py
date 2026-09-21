"""Persistence: the encrypted config file and saved conversation history."""

import json
import datetime
from pathlib import Path
from typing import Dict, List

from cryptography.fernet import Fernet, InvalidToken

from .paths import APP_DIR, CONFIG_FILE, HISTORY_DIR


class WrongPasswordError(Exception):
    """The config could not be decrypted: wrong master password or a corrupted file."""


# ── Config ────────────────────────────────────────────────────────────────────

EMPTY_CONFIG: Dict[str, List] = {"endpoints": [], "system_prompts": [], "mcp_servers": []}


def load_config(fernet: Fernet) -> dict:
    if not CONFIG_FILE.exists():
        return {**{k: [] for k in EMPTY_CONFIG}, "settings": {}}
    try:
        config = json.loads(fernet.decrypt(CONFIG_FILE.read_bytes()))
    except InvalidToken:
        raise WrongPasswordError from None
    for key in EMPTY_CONFIG:  # configs saved before a section existed lack the key
        config.setdefault(key, [])
    config.setdefault("settings", {})  # global defaults, e.g. {"show_usage": true}
    return config


def default_show_usage(config: dict) -> bool:
    return bool(config.get("settings", {}).get("show_usage", False))


def save_config(config: dict, fernet: Fernet) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_bytes(fernet.encrypt(json.dumps(config).encode()))


# ── Conversation history ──────────────────────────────────────────────────────

def list_sessions() -> List[dict]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for f in sorted(HISTORY_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                **data,
                "_path": f,
                "updated": datetime.datetime.fromtimestamp(
                    f.stat().st_mtime
                ).strftime("%Y-%m-%d %H:%M"),
                "message_count": len(data.get("messages", [])),
            })
        except Exception:
            pass
    return sessions


def save_session(sess: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path: Path = sess["_path"]
    # api_key is never written to disk: it lives in the encrypted config and is
    # re-read from there on resume (see resume_session).
    serialisable = {k: v for k, v in sess.items() if not k.startswith("_") and k != "api_key"}
    path.write_text(json.dumps(serialisable, indent=2, ensure_ascii=False), encoding="utf-8")
