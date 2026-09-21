import builtins
import getpass
from types import SimpleNamespace

import pytest

from chatcli import store
from chatcli.models import Config, Endpoint, McpServerConfig, SystemPrompt
from chatcli.ui import menus


def flat(buf) -> str:
    return " ".join(buf.getvalue().replace("│", " ").split())


def reload(fernet) -> Config:
    return store.load_config(fernet)


@pytest.fixture
def api_key(monkeypatch):
    secrets = iter(["SECRET-1", "SECRET-2", "SECRET-3"])
    monkeypatch.setattr(getpass, "getpass", lambda *_: next(secrets))


# ── endpoints ─────────────────────────────────────────────────────────────────

def test_add_endpoint_is_saved_with_all_fields(fernet, out, prompts, api_key):
    cfg = Config()
    prompts("1", "ep1", "http://a/v1", "gpt-x", "on", "high", "0")
    menus.manage_endpoints(cfg, fernet)
    (ep,) = reload(fernet).endpoints
    assert ep == Endpoint("ep1", "http://a/v1", "SECRET-1", "gpt-x", thinking=True, reasoning_effort="high")
    assert "Endpoint 'ep1' saved." in flat(out)


def test_thinking_off_skips_the_effort_question_and_stores_default(fernet, out, prompts, api_key):
    cfg = Config()
    prompts("1", "ep1", "http://a/v1", "m", "off", "0")     # no effort answer available
    menus.manage_endpoints(cfg, fernet)
    ep = reload(fernet).endpoints[0]
    assert (ep.thinking, ep.reasoning_effort) == (False, "default")


def test_delete_endpoint_removes_and_persists_and_zero_cancels(fernet, out, prompts, endpoint):
    cfg = Config(endpoints=[endpoint, Endpoint("other", "u", "k", "m")])
    prompts("2", "0", "2", "1", "0")
    menus.manage_endpoints(cfg, fernet)
    assert [e.name for e in reload(fernet).endpoints] == ["other"]
    assert "Deleted 'ep'." in flat(out)


def test_first_delete_cancelled_writes_nothing(fernet, out, prompts, endpoint):
    prompts("2", "0", "0")
    menus.manage_endpoints(Config(endpoints=[endpoint]), fernet)
    assert not store.CONFIG_FILE.exists()


def test_list_endpoints_when_empty_and_when_populated(fernet, out, prompts, endpoint):
    prompts("3", "0")
    menus.manage_endpoints(Config(), fernet)
    assert "No endpoints saved yet." in flat(out)

    out.truncate(0)
    out.seek(0)
    endpoint.thinking = False
    prompts("3", "0")
    menus.manage_endpoints(Config(endpoints=[endpoint]), fernet)
    text = flat(out)
    assert "ep http://x/v1 m off" in text and "Reasoning" in text


def test_change_reasoning_settings_updates_and_saves(fernet, out, prompts, endpoint):
    cfg = Config(endpoints=[endpoint])
    prompts("4", "1", "on", "low", "0")
    menus.manage_endpoints(cfg, fernet)
    ep = reload(fernet).endpoints[0]
    assert (ep.thinking, ep.reasoning_effort) == (True, "low")
    assert "Updated 'ep'." in flat(out)


def test_change_reasoning_cancelled_changes_nothing(fernet, out, prompts, endpoint):
    prompts("4", "0", "0")
    menus.manage_endpoints(Config(endpoints=[endpoint]), fernet)
    assert not store.CONFIG_FILE.exists()


def test_endpoint_menu_offers_reasoning_settings_but_prompt_menu_does_not(fernet, out, prompts):
    prompts("0")
    menus.manage_endpoints(Config(), fernet)
    assert "4. Reasoning settings" in flat(out)
    out.truncate(0)
    out.seek(0)
    prompts("0")
    menus.manage_prompts(Config(), fernet)
    assert "Reasoning" not in flat(out) and "3. List" in flat(out)


@pytest.mark.parametrize("junk", ["9", "abc", "", "-1", "5", "1.5"])
def test_unknown_menu_choices_are_ignored(fernet, out, prompts, junk):
    prompts(junk, "0")
    menus.manage_endpoints(Config(), fernet)
    assert not store.CONFIG_FILE.exists()


# ── system prompts ────────────────────────────────────────────────────────────

def test_add_prompt_reads_lines_until_the_terminator(fernet, out, prompts, monkeypatch):
    lines = iter(["line one", "", "line three", "---"])
    monkeypatch.setattr(builtins, "input", lambda *_: next(lines))
    prompts("1", "Coder", "0")
    menus.manage_prompts(Config(), fernet)
    (p,) = reload(fernet).system_prompts
    assert p == SystemPrompt("Coder", "line one\n\nline three")


def test_prompt_list_shows_a_single_line_preview_cut_at_80_chars(fernet, out, prompts):
    cfg = Config(system_prompts=[SystemPrompt("short", "a\nb"), SystemPrompt("long", "x" * 100)])
    prompts("3", "0")
    menus.manage_prompts(cfg, fernet)
    text = flat(out)
    assert "short a b" in text and "x" * 80 + "…" in text and "x" * 81 not in text


def test_delete_prompt(fernet, out, prompts, system_prompt):
    prompts("2", "1", "0")
    cfg = Config(system_prompts=[system_prompt])
    menus.manage_prompts(cfg, fernet)
    assert reload(fernet).system_prompts == []


# ── MCP servers ───────────────────────────────────────────────────────────────

class FakeMcp:
    def __init__(self, errors=None, server_tools=None, start_result=True):
        self.errors, self.server_tools, self.started = errors or {}, server_tools or {}, []
        self.start_result = start_result

    def start(self, servers):
        self.started.append([s.name for s in servers])
        return self.start_result


def test_add_mcp_server_parses_quoted_arguments_and_collects_env_vars(fernet, out, prompts, api_key):
    prompts("1", "srv", "python", '"my script.py" --flag "two words"', "API_KEY", "OTHER", "", "0")
    menus.manage_mcp_servers(Config(), fernet, FakeMcp())
    (s,) = reload(fernet).mcp_servers
    assert s.command == "python" and s.args == ["my script.py", "--flag", "two words"]
    assert s.env == {"API_KEY": "SECRET-1", "OTHER": "SECRET-2"}
    assert "MCP server 'srv' saved." in flat(out)


def test_mcp_list_shows_connection_status_per_server(fernet, out, prompts):
    cfg = Config(mcp_servers=[McpServerConfig("ok", "python", ["a.py"]),
                              McpServerConfig("bad", "nope"),
                              McpServerConfig("idle", "x", env={"K": "v"})])
    mgr = FakeMcp(errors={"bad": "FileNotFoundError: nope"}, server_tools={"ok": ["t1", "t2"]})
    prompts("3", "0")
    menus.manage_mcp_servers(cfg, fernet, mgr)
    text = flat(out)
    assert "connected: t1, t2" in text
    assert "failed: FileNotFoundError: nope" in text
    assert "not connected yet" in text
    assert "python a.py" in text and "K" in text


def test_mcp_list_never_shows_secret_env_values(fernet, out, prompts):
    cfg = Config(mcp_servers=[McpServerConfig("s", "x", env={"API_KEY": "hunter2"})])
    prompts("3", "0")
    menus.manage_mcp_servers(cfg, fernet, FakeMcp())
    assert "hunter2" not in out.getvalue() and "API_KEY" in out.getvalue()


def test_prepare_mcp_connects_only_when_servers_are_configured(out):
    mgr = FakeMcp()
    menus.prepare_mcp(Config(), mgr)
    assert mgr.started == []
    menus.prepare_mcp(Config(mcp_servers=[McpServerConfig("s", "x")]), mgr)
    assert mgr.started == [["s"]]


def test_prepare_mcp_reports_servers_that_failed_to_start(out):
    mgr = FakeMcp(errors={"bad": "RuntimeError: boom"})
    menus.prepare_mcp(Config(mcp_servers=[McpServerConfig("bad", "x")]), mgr)
    text = flat(out)
    assert "MCP server 'bad' failed to start: RuntimeError: boom" in text and "mcp_bad.log" in text


def test_prepare_mcp_stays_quiet_when_the_same_servers_are_already_running(out):
    mgr = FakeMcp(errors={"bad": "boom"}, start_result=False)
    menus.prepare_mcp(Config(mcp_servers=[McpServerConfig("bad", "x")]), mgr)
    assert "failed to start" not in out.getvalue()


def test_prepare_mcp_explains_when_the_mcp_package_is_missing(out, monkeypatch):
    monkeypatch.setattr(menus, "MCP_AVAILABLE", False)
    mgr = FakeMcp()
    menus.prepare_mcp(Config(mcp_servers=[McpServerConfig("s", "x")]), mgr)
    assert "isn't installed" in flat(out) and mgr.started == []


# ── settings ──────────────────────────────────────────────────────────────────

def test_settings_toggle_persists_and_flips_back(fernet, out, prompts):
    cfg = Config()
    prompts("1", "0")
    menus.manage_settings(cfg, fernet)
    assert reload(fernet).settings.show_usage is True
    prompts("1", "0")
    menus.manage_settings(cfg, fernet)
    assert reload(fernet).settings.show_usage is False
    assert "Usage stats default: on." in flat(out)


# ── the generic menu itself ───────────────────────────────────────────────────

def test_a_new_menu_is_just_a_description(fernet, out, prompts, monkeypatch):
    """The point of CollectionMenu: a fourth collection needs no new loop."""
    cfg = Config()
    cfg.widgets = []
    spec = menus.CollectionMenu(
        title="Widgets", attr="widgets", label=lambda w: w.name, delete_title="Delete widget",
        empty_message="No widgets.", columns=("Name",), row=lambda w: (w.name,),
        prompt_new=lambda: SimpleNamespace(name="w1", to_dict=lambda: {}),
        saved_message=lambda w: f"Widget '{w.name}' saved.",
    )
    monkeypatch.setattr(menus, "save_config", lambda c, f: None)   # Config has no widgets field
    prompts("3", "1", "3", "2", "1", "3", "0")
    menus.run_collection_menu(spec, cfg, fernet)
    text = flat(out)
    assert "No widgets." in text and "Widget 'w1' saved." in text and "Deleted 'w1'." in text
    assert cfg.widgets == []
