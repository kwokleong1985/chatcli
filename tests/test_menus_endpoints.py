import getpass

import pytest

from chatcli import store
from chatcli.models import Config, Endpoint
from chatcli.ui.menus import endpoints
from chatcli.ui.menus import prompts as prompts_menu


def flat(buf) -> str:
    return " ".join(buf.getvalue().replace("│", " ").split())


def reload(fernet) -> Config:
    return store.load_config(fernet)


@pytest.fixture
def api_key(monkeypatch):
    secrets = iter(["SECRET-1", "SECRET-2", "SECRET-3"])
    monkeypatch.setattr(getpass, "getpass", lambda *_: next(secrets))


def test_add_endpoint_is_saved_with_all_fields(fernet, out, prompts, api_key):
    cfg = Config()
    prompts("1", "ep1", "http://a/v1", "gpt-x", "on", "high", "0")
    endpoints.manage_endpoints(cfg, fernet)
    (ep,) = reload(fernet).endpoints
    assert ep == Endpoint("ep1", "http://a/v1", "SECRET-1", "gpt-x", thinking=True, reasoning_effort="high")
    assert "Endpoint 'ep1' saved." in flat(out)


def test_thinking_off_skips_the_effort_question_and_stores_default(fernet, out, prompts, api_key):
    cfg = Config()
    prompts("1", "ep1", "http://a/v1", "m", "off", "0")     # no effort answer available
    endpoints.manage_endpoints(cfg, fernet)
    ep = reload(fernet).endpoints[0]
    assert (ep.thinking, ep.reasoning_effort) == (False, "default")


def test_delete_endpoint_removes_and_persists_and_zero_cancels(fernet, out, prompts, endpoint):
    cfg = Config(endpoints=[endpoint, Endpoint("other", "u", "k", "m")])
    prompts("2", "0", "2", "1", "0")
    endpoints.manage_endpoints(cfg, fernet)
    assert [e.name for e in reload(fernet).endpoints] == ["other"]
    assert "Deleted 'ep'." in flat(out)


def test_first_delete_cancelled_writes_nothing(fernet, out, prompts, endpoint):
    prompts("2", "0", "0")
    endpoints.manage_endpoints(Config(endpoints=[endpoint]), fernet)
    assert not store.CONFIG_FILE.exists()


def test_list_endpoints_when_empty_and_when_populated(fernet, out, prompts, endpoint):
    prompts("3", "0")
    endpoints.manage_endpoints(Config(), fernet)
    assert "No endpoints saved yet." in flat(out)

    out.truncate(0)
    out.seek(0)
    endpoint.thinking = False
    prompts("3", "0")
    endpoints.manage_endpoints(Config(endpoints=[endpoint]), fernet)
    text = flat(out)
    assert "ep http://x/v1 m off" in text and "Reasoning" in text


def test_change_reasoning_settings_updates_and_saves(fernet, out, prompts, endpoint):
    cfg = Config(endpoints=[endpoint])
    prompts("4", "1", "on", "low", "0")
    endpoints.manage_endpoints(cfg, fernet)
    ep = reload(fernet).endpoints[0]
    assert (ep.thinking, ep.reasoning_effort) == (True, "low")
    assert "Updated 'ep'." in flat(out)


def test_change_reasoning_cancelled_changes_nothing(fernet, out, prompts, endpoint):
    prompts("4", "0", "0")
    endpoints.manage_endpoints(Config(endpoints=[endpoint]), fernet)
    assert not store.CONFIG_FILE.exists()


def test_endpoint_menu_offers_reasoning_settings_but_prompt_menu_does_not(fernet, out, prompts):
    prompts("0")
    endpoints.manage_endpoints(Config(), fernet)
    assert "4. Reasoning settings" in flat(out)
    out.truncate(0)
    out.seek(0)
    prompts("0")
    prompts_menu.manage_prompts(Config(), fernet)
    assert "Reasoning" not in flat(out) and "3. List" in flat(out)


@pytest.mark.parametrize("junk", ["9", "abc", "", "-1", "5", "1.5"])
def test_unknown_menu_choices_are_ignored(fernet, out, prompts, junk):
    prompts(junk, "0")
    endpoints.manage_endpoints(Config(), fernet)
    assert not store.CONFIG_FILE.exists()
