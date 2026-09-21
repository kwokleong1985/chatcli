"""Filesystem locations for ChatCLI's config, history and logs."""

from pathlib import Path

APP_DIR = Path.home() / ".chatcli"
CONFIG_FILE = APP_DIR / "config.enc"
SALT_FILE = APP_DIR / "salt.bin"
HISTORY_DIR = APP_DIR / "history"
LOG_DIR = APP_DIR / "logs"
API_ERROR_LOG = LOG_DIR / "api_errors.jsonl"
