import json
import os

import pytest

from chatcli import paths, store
from chatcli.models import CONFIG_VERSION, Config, Endpoint


def _write_raw_config(fernet, data: dict) -> None:
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    paths.CONFIG_FILE.write_bytes(fernet.encrypt(json.dumps(data).encode()))


# ── config ────────────────────────────────────────────────────────────────────

def test_missing_config_loads_as_empty(fernet):
    assert store.load_config(fernet) == Config()


def test_config_round_trip(fernet, endpoint):
    cfg = Config(endpoints=[endpoint])
    store.save_config(cfg, fernet)
    assert store.load_config(fernet) == cfg


def test_config_file_is_encrypted_on_disk(fernet, endpoint):
    store.save_config(Config(endpoints=[endpoint]), fernet)
    raw = paths.CONFIG_FILE.read_bytes()
    assert b"KEY" not in raw and b"http://x/v1" not in raw


def test_legacy_config_without_version_or_new_fields_loads_with_defaults(fernet):
    _write_raw_config(fernet, {
        "endpoints": [{"name": "old", "base_url": "u", "api_key": "K", "model": "m"}],
        "system_prompts": [{"name": "p", "content": "c"}],
        "settings": {"show_usage": True},
    })
    cfg = store.load_config(fernet)
    ep = cfg.endpoints[0]
    assert (ep.thinking, ep.reasoning_effort) == (True, "default")
    assert cfg.mcp_servers == [] and cfg.settings.show_usage is True


def test_saving_a_legacy_config_upgrades_it_to_the_current_shape(fernet):
    _write_raw_config(fernet, {"endpoints": [{"name": "old", "base_url": "u", "api_key": "K", "model": "m"}]})
    store.save_config(store.load_config(fernet), fernet)
    saved = json.loads(fernet.decrypt(paths.CONFIG_FILE.read_bytes()))
    assert saved["version"] == CONFIG_VERSION
    assert saved["endpoints"][0]["reasoning_effort"] == "default"


def test_wrong_password_and_corruption_raise_wrong_password_error(fernet):
    store.save_config(Config(), fernet)
    from cryptography.fernet import Fernet
    with pytest.raises(store.WrongPasswordError):
        store.load_config(Fernet(Fernet.generate_key()))
    paths.CONFIG_FILE.write_bytes(b"not a fernet token")
    with pytest.raises(store.WrongPasswordError):
        store.load_config(fernet)


def test_config_from_a_newer_version_is_refused(fernet):
    _write_raw_config(fernet, {"version": CONFIG_VERSION + 1, "endpoints": []})
    with pytest.raises(store.UnsupportedConfigError):
        store.load_config(fernet)


def test_failed_config_save_keeps_the_old_file_and_leaves_no_temp_files(fernet, endpoint, monkeypatch):
    store.save_config(Config(endpoints=[endpoint]), fernet)
    before = paths.CONFIG_FILE.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("disk yanked")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.save_config(Config(endpoints=[endpoint, Endpoint("new", "u", "k", "m")]), fernet)
    monkeypatch.undo()

    assert paths.CONFIG_FILE.read_bytes() == before
    assert not list(paths.APP_DIR.glob("*.tmp"))


# ── history ───────────────────────────────────────────────────────────────────

def _write_history(name: str, data: dict, mtime: float | None = None):
    paths.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    p = paths.HISTORY_DIR / name
    p.write_text(json.dumps(data), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


LEGACY = {"name": "old", "endpoint_name": "e", "base_url": "u", "model": "m",
          "prompt_name": "p", "system_prompt": "s", "thinking": True,
          "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
          "updated": "stale", "message_count": 99}   # keys older versions wrote by mistake


def test_list_sessions_is_empty_without_a_history_folder():
    assert store.list_sessions() == []


def test_list_sessions_orders_newest_first_and_labels_them():
    _write_history("a.json", {**LEGACY, "name": "older"}, mtime=1_000_000_000)
    _write_history("b.json", {**LEGACY, "name": "newer"}, mtime=1_500_000_000)
    sessions = store.list_sessions()
    assert [s.name for s in sessions] == ["newer", "older"]
    assert sessions[0].path == paths.HISTORY_DIR / "b.json"
    assert len(sessions[0].updated) == len("2017-07-14 02:40")
    assert sessions[0].updated != "stale"


def test_list_sessions_skips_unreadable_files_and_ignores_legacy_stray_keys():
    _write_history("good.json", LEGACY)
    (paths.HISTORY_DIR / "junk.json").write_text("{not json", encoding="utf-8")
    _write_history("incomplete.json", {"name": "missing required fields"})
    (s,) = store.list_sessions()
    assert s.name == "old" and s.message_count == 2
    assert s.reasoning_effort == "default" and s.tools is True and s.show_usage is None


def test_save_session_round_trips_and_never_writes_the_api_key(session):
    session.messages.append({"role": "user", "content": "héllo ✓"})
    store.save_session(session)
    text = session.path.read_text(encoding="utf-8")
    assert "KEY" not in text and '"path"' not in text and '"api_key"' not in text
    (loaded,) = store.list_sessions()
    assert loaded.messages == session.messages and loaded.api_key == ""


def test_saving_a_resumed_legacy_session_drops_the_stray_keys():
    p = _write_history("old.json", LEGACY)
    (s,) = store.list_sessions()
    store.save_session(s)
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert "updated" not in on_disk and "message_count" not in on_disk


def test_failed_session_save_keeps_the_previous_history(session, monkeypatch):
    store.save_session(session)
    before = session.path.read_text(encoding="utf-8")
    session.messages.append({"role": "user", "content": "new"})

    def fail(*args, **kwargs):
        raise OSError("disk yanked")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.save_session(session)
    monkeypatch.undo()

    assert session.path.read_text(encoding="utf-8") == before
    assert not list(paths.HISTORY_DIR.glob("*.tmp"))
