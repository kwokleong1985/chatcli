import pytest

from chatcli import crypto, store
from chatcli.models import Config, Endpoint
from chatcli.ui.menus import settings


def flat(buf) -> str:
    return " ".join(buf.getvalue().replace("│", " ").split())


def reload(fernet) -> Config:
    return store.load_config(fernet)


def test_settings_toggle_persists_and_flips_back(fernet, out, prompts):
    cfg = Config()
    prompts("1", "0")
    settings.manage_settings(cfg, fernet)
    assert reload(fernet).settings.show_usage is True
    prompts("1", "0")
    settings.manage_settings(cfg, fernet)
    assert reload(fernet).settings.show_usage is False
    assert "Usage stats default: on." in flat(out)


# ── export / import ───────────────────────────────────────────────────────────

def test_export_config_writes_a_portable_file(fernet, out, prompts, endpoint, tmp_path):
    crypto.get_or_create_salt()
    store.save_config(Config(endpoints=[endpoint]), fernet)
    dest = tmp_path / "export.enc"
    prompts("2", str(dest), "0")
    settings.manage_settings(Config(endpoints=[endpoint]), fernet)
    assert dest.exists()
    assert "Exported to" in flat(out)


def test_export_with_nothing_saved_warns_instead_of_writing(fernet, out, prompts, tmp_path):
    dest = tmp_path / "export.enc"
    prompts("2", str(dest), "0")
    settings.manage_settings(Config(), fernet)
    assert not dest.exists()
    assert "Nothing saved yet" in flat(out)


def test_import_config_replaces_local_config_and_exits(fernet, out, prompts, endpoint, tmp_path):
    crypto.get_or_create_salt()
    store.save_config(Config(endpoints=[endpoint]), fernet)
    dest = tmp_path / "export.enc"
    store.export_config(dest)

    prompts("3", str(dest), "y")
    with pytest.raises(SystemExit) as exc:
        settings.manage_settings(Config(endpoints=[Endpoint("other", "u", "k", "m")]), fernet)
    assert exc.value.code == 0
    assert reload(fernet).endpoints[0].name == "ep"
    assert "Restart ChatCLI" in flat(out)


def test_import_config_missing_file_shows_an_error(fernet, out, prompts, tmp_path):
    prompts("3", str(tmp_path / "missing.enc"), "0")
    settings.manage_settings(Config(), fernet)
    assert "File not found." in flat(out)


def test_import_config_cancelled_when_declined(fernet, out, prompts, endpoint, tmp_path):
    crypto.get_or_create_salt()
    store.save_config(Config(endpoints=[endpoint]), fernet)
    dest = tmp_path / "export.enc"
    store.export_config(dest)
    prompts("3", str(dest), "n", "0")
    settings.manage_settings(Config(endpoints=[endpoint]), fernet)
    assert reload(fernet).endpoints[0].name == "ep"


def test_import_config_rejects_a_bogus_file(fernet, out, prompts, tmp_path):
    bogus = tmp_path / "bogus.enc"
    bogus.write_text("not json")
    prompts("3", str(bogus), "0")
    settings.manage_settings(Config(), fernet)
    assert "Not a chatcli export file" in flat(out)
