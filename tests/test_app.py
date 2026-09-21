"""End-to-end: the real entry point with scripted input and a fake model."""

import getpass

import pytest
from cryptography.fernet import Fernet

from chatcli import app, paths, store
from chatcli.models import CONFIG_VERSION, Config, Endpoint, SystemPrompt
from chatcli.ui import chat


class FakeMcp:
    errors, server_tools, tool_names = {}, {}, []

    def start(self, servers):
        return True

    def close(self):
        pass


@pytest.fixture(autouse=True)
def isolate(monkeypatch, fernet):
    monkeypatch.setattr(getpass, "getpass", lambda *_: "pw")
    monkeypatch.setattr(app, "make_fernet", lambda password: fernet)   # skip the deliberately slow KDF
    monkeypatch.setattr(app, "McpManager", FakeMcp)
    monkeypatch.setattr(app.atexit, "register", lambda *a, **k: None)


def flat(buf) -> str:
    return " ".join(buf.getvalue().replace("│", " ").split())


def test_wrong_password_exits_with_status_1(out, prompts):
    store.save_config(Config(), Fernet(Fernet.generate_key()))      # saved under a different key
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 1 and "Wrong password" in flat(out)


def test_a_config_from_a_newer_version_exits_with_status_1_and_is_left_alone(out, fernet):
    import json
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    blob = fernet.encrypt(json.dumps({"version": CONFIG_VERSION + 1}).encode())
    paths.CONFIG_FILE.write_bytes(blob)
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 1 and "newer ChatCLI" in flat(out)
    assert paths.CONFIG_FILE.read_bytes() == blob


def test_menu_shows_every_option_and_exits(out, prompts):
    prompts("0")
    app.main()
    text = flat(out)
    for entry in ("New conversation", "Resume conversation", "Manage endpoints",
                  "Manage system prompts", "Manage MCP servers", "Settings", "Exit", "Goodbye."):
        assert entry in text


def test_starting_or_resuming_without_setup_explains_what_is_missing(out, prompts, fernet):
    prompts("1", "2", "0")
    app.main()
    assert flat(out).count("No endpoints saved") == 2
    out.truncate(0)
    out.seek(0)
    store.save_config(Config(endpoints=[Endpoint("e", "u", "k", "m")]), fernet)
    prompts("1", "0")
    app.main()
    assert "No system prompts saved" in flat(out)


def test_first_run_setup_then_a_conversation_that_survives_a_restart(out, prompts, monkeypatch, fernet):
    monkeypatch.setattr(getpass, "getpass", lambda *_: "API-KEY")      # only asked for while adding the endpoint
    monkeypatch.setattr(app, "make_fernet", lambda password: fernet)
    lines = iter(["be helpful", "---"])
    monkeypatch.setattr("builtins.input", lambda *_: next(lines))
    monkeypatch.setattr(chat, "ask_model", lambda sess, text, mcp, on_tool: (f"echo: {text}", None))

    prompts(
        "3", "1", "ep", "http://x/v1", "model-x", "off", "0",       # add an endpoint
        "4", "1", "Helper", "0",                                     # add a system prompt
        "1", "1", "1", "first chat", "hello", "/quit",               # new conversation, one turn
        "0",
    )
    app.main()

    cfg = store.load_config(fernet)
    assert [e.name for e in cfg.endpoints] == ["ep"] and cfg.system_prompts[0].content == "be helpful"
    (s,) = store.list_sessions()
    assert s.name == "first chat" and s.thinking is False
    assert s.messages == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "echo: hello"}]

    # "Restart": resume it, the key comes back from the encrypted config, not from history
    seen = []
    monkeypatch.setattr(chat, "ask_model", lambda sess, text, mcp, on_tool: (seen.append(sess.api_key) or "again", None))
    prompts("2", "1", "more", "/quit", "0")
    app.main()
    assert seen == ["API-KEY"]
    assert "API-KEY" not in s.path.read_text(encoding="utf-8")
    assert len(store.list_sessions()[0].messages) == 4


def test_mcp_servers_connect_when_a_conversation_starts(out, prompts, monkeypatch, fernet):
    from chatcli.models import McpServerConfig
    started = []
    monkeypatch.setattr(FakeMcp, "start", lambda self, servers: started.append([s.name for s in servers]) or True)
    store.save_config(Config(endpoints=[Endpoint("e", "u", "k", "m")], system_prompts=[SystemPrompt("P", "c")],
                             mcp_servers=[McpServerConfig("srv", "python")]), fernet)
    prompts("1", "1", "1", "chat", "/quit", "0")
    app.main()
    assert started == [["srv"]]


def test_mcp_server_processes_are_shut_down_however_the_app_exits(out, prompts, monkeypatch):
    registered = []
    monkeypatch.setattr(app.atexit, "register", lambda fn, *a, **k: registered.append(fn))
    prompts("0")
    app.main()
    assert len(registered) == 1
    assert registered[0].__name__ == "close" and isinstance(registered[0].__self__, FakeMcp)
