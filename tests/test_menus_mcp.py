import getpass

import pytest

from chatcli import store
from chatcli.models import Config, McpServerConfig
from chatcli.ui.menus import mcp as mcp_menu


def flat(buf) -> str:
    return " ".join(buf.getvalue().replace("│", " ").split())


def reload(fernet) -> Config:
    return store.load_config(fernet)


@pytest.fixture
def api_key(monkeypatch):
    secrets = iter(["SECRET-1", "SECRET-2", "SECRET-3"])
    monkeypatch.setattr(getpass, "getpass", lambda *_: next(secrets))


class FakeMcp:
    def __init__(self, errors=None, server_tools=None, start_result=True):
        self.errors, self.server_tools, self.started = errors or {}, server_tools or {}, []
        self.start_result = start_result

    def start(self, servers):
        self.started.append([s.name for s in servers])
        return self.start_result


def test_add_mcp_server_parses_quoted_arguments_and_collects_env_vars(fernet, out, prompts, api_key):
    prompts("1", "srv", "python", '"my script.py" --flag "two words"', "API_KEY", "OTHER", "", "0")
    mcp_menu.manage_mcp_servers(Config(), fernet, FakeMcp())
    (s,) = reload(fernet).mcp_servers
    assert s.command == "python" and s.args == ["my script.py", "--flag", "two words"]
    assert s.env == {"API_KEY": "SECRET-1", "OTHER": "SECRET-2"}
    assert "MCP server 'srv' saved." in flat(out)


def test_edit_mcp_server_changes_path_without_retyping_env_vars(fernet, out, prompts):
    cfg = Config(mcp_servers=[McpServerConfig("srv", "python", ["old.py"], env={"API_KEY": "secret"})])
    prompts("4", "1", "python", "new_script.py", "y", "0")
    mcp_menu.manage_mcp_servers(cfg, fernet, FakeMcp())
    (s,) = reload(fernet).mcp_servers
    assert s.args == ["new_script.py"]
    assert s.env == {"API_KEY": "secret"}
    assert "MCP server 'srv' updated." in flat(out)


def test_mcp_list_shows_connection_status_per_server(fernet, out, prompts):
    cfg = Config(mcp_servers=[McpServerConfig("ok", "python", ["a.py"]),
                              McpServerConfig("bad", "nope"),
                              McpServerConfig("idle", "x", env={"K": "v"})])
    mgr = FakeMcp(errors={"bad": "FileNotFoundError: nope"}, server_tools={"ok": ["t1", "t2"]})
    prompts("3", "0")
    mcp_menu.manage_mcp_servers(cfg, fernet, mgr)
    text = flat(out)
    assert "connected: t1, t2" in text
    assert "failed: FileNotFoundError: nope" in text
    assert "not connected yet" in text
    assert "python a.py" in text and "K" in text


def test_mcp_list_never_shows_secret_env_values(fernet, out, prompts):
    cfg = Config(mcp_servers=[McpServerConfig("s", "x", env={"API_KEY": "hunter2"})])
    prompts("3", "0")
    mcp_menu.manage_mcp_servers(cfg, fernet, FakeMcp())
    assert "hunter2" not in out.getvalue() and "API_KEY" in out.getvalue()


def test_prepare_mcp_connects_only_when_servers_are_configured(out):
    mgr = FakeMcp()
    mcp_menu.prepare_mcp(Config(), mgr)
    assert mgr.started == []
    mcp_menu.prepare_mcp(Config(mcp_servers=[McpServerConfig("s", "x")]), mgr)
    assert mgr.started == [["s"]]


def test_prepare_mcp_reports_servers_that_failed_to_start(out):
    mgr = FakeMcp(errors={"bad": "RuntimeError: boom"})
    mcp_menu.prepare_mcp(Config(mcp_servers=[McpServerConfig("bad", "x")]), mgr)
    text = flat(out)
    assert "MCP server 'bad' failed to start: RuntimeError: boom" in text and "mcp_bad.log" in text


def test_prepare_mcp_stays_quiet_when_the_same_servers_are_already_running(out):
    mgr = FakeMcp(errors={"bad": "boom"}, start_result=False)
    mcp_menu.prepare_mcp(Config(mcp_servers=[McpServerConfig("bad", "x")]), mgr)
    assert "failed to start" not in out.getvalue()


def test_prepare_mcp_explains_when_the_mcp_package_is_missing(out, monkeypatch):
    monkeypatch.setattr(mcp_menu, "MCP_AVAILABLE", False)
    mgr = FakeMcp()
    mcp_menu.prepare_mcp(Config(mcp_servers=[McpServerConfig("s", "x")]), mgr)
    assert "isn't installed" in flat(out) and mgr.started == []
