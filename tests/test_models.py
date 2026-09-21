from pathlib import Path

from chatcli.models import CONFIG_VERSION, Config, Endpoint, McpServerConfig, Session, Settings


def test_from_dict_fills_defaults_and_ignores_unknown_keys():
    ep = Endpoint.from_dict({"name": "n", "base_url": "u", "api_key": "k", "model": "m", "future_field": 1})
    assert ep.thinking is True and ep.reasoning_effort == "default"
    assert "future_field" not in ep.to_dict()


def test_mcp_server_defaults_are_not_shared_between_instances():
    a, b = McpServerConfig("a", "cmd"), McpServerConfig("b", "cmd")
    a.args.append("x")
    a.env["K"] = "v"
    assert b.args == [] and b.env == {}


def test_config_from_empty_dict_gives_empty_sections():
    cfg = Config.from_dict({})
    assert (cfg.endpoints, cfg.system_prompts, cfg.mcp_servers) == ([], [], [])
    assert cfg.settings == Settings(show_usage=False)


def test_config_round_trip_and_version():
    cfg = Config(
        endpoints=[Endpoint("e", "u", "k", "m", thinking=False, reasoning_effort="low")],
        mcp_servers=[McpServerConfig("s", "python", ["a.py"], {"K": "v"})],
        settings=Settings(show_usage=True),
    )
    data = cfg.to_dict()
    assert data["version"] == CONFIG_VERSION
    assert Config.from_dict(data) == cfg


def _session(**kw) -> Session:
    base = dict(name="n", endpoint_name="e", base_url="u", model="m", prompt_name="p", system_prompt="s")
    return Session(**{**base, **kw})


def test_session_to_dict_leaves_out_runtime_only_fields():
    s = _session(api_key="SECRET", path=Path("x.json"), updated="2020-01-01 00:00")
    data = s.to_dict()
    assert not {"api_key", "path", "updated"} & data.keys()
    assert "SECRET" not in str(data)


def test_session_from_dict_never_reads_runtime_only_fields():
    s = Session.from_dict({
        "name": "n", "endpoint_name": "e", "base_url": "u", "model": "m", "prompt_name": "p",
        "system_prompt": "s", "api_key": "LEAKED", "path": "/etc/x", "updated": "old", "message_count": 9,
    })
    assert s.api_key == "" and s.path is None and s.updated == ""


def test_session_defaults_for_fields_old_history_files_lack():
    s = _session()
    assert s.reasoning_effort == "default" and s.tools is True and s.show_usage is None and s.messages == []


def test_session_message_count_tracks_messages():
    s = _session()
    assert s.message_count == 0
    s.messages.append({"role": "user", "content": "hi"})
    assert s.message_count == 1


def test_session_messages_are_not_shared_between_instances():
    a, b = _session(), _session()
    a.messages.append({"role": "user", "content": "x"})
    assert b.messages == []
