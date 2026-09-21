import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from chatcli import mcp_client
from chatcli.mcp_client import McpManager, _clean_schema, safe_name
from chatcli.models import McpServerConfig

ROOT = Path(__file__).resolve().parent.parent


def test_safe_name_replaces_anything_but_word_characters_and_dashes():
    assert safe_name("my server/v1.0") == "my_server_v1_0"
    assert safe_name("ok-name_1") == "ok-name_1"


# ── _clean_schema ─────────────────────────────────────────────────────────────

def test_clean_schema_drops_title_metadata():
    assert _clean_schema({"title": "Args", "type": "object"}) == {"type": "object"}


def test_clean_schema_keeps_a_property_that_is_merely_named_title():
    schema = {"type": "object", "properties": {"title": {"type": "string", "title": "Title"}}}
    assert _clean_schema(schema) == {"type": "object", "properties": {"title": {"type": "string"}}}


def test_clean_schema_collapses_optional_into_the_plain_type():
    schema = {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None, "description": "n"}
    assert _clean_schema(schema) == {"type": "integer", "description": "n"}


def test_clean_schema_keeps_real_unions_and_non_null_defaults():
    union = {"anyOf": [{"type": "integer"}, {"type": "string"}]}
    assert _clean_schema(union) == union
    assert _clean_schema({"type": "integer", "default": 10}) == {"type": "integer", "default": 10}


def test_clean_schema_recurses_into_lists_and_nested_objects():
    schema = {"items": [{"title": "x", "type": "string"}], "properties": {"a": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}
    assert _clean_schema(schema) == {"items": [{"type": "string"}], "properties": {"a": {"type": "string"}}}


# ── call(): tool results as text ──────────────────────────────────────────────

class FakeSession:
    def __init__(self, result=None, exc=None, delay=0.0):
        self.result, self.exc, self.delay = result, exc, delay
        self.seen = []

    async def call_tool(self, name, args):
        self.seen.append((name, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result


def _text(s):
    return SimpleNamespace(type="text", text=s)


@pytest.fixture
def manager():
    """A manager with a live event loop but no real servers; tests register fake sessions."""
    m = McpManager()
    m._loop = asyncio.new_event_loop()
    threading.Thread(target=m._loop.run_forever, daemon=True).start()
    yield m
    m._loop.call_soon_threadsafe(m._loop.stop)


def _register(m, session, exposed="t", original="orig"):
    m._tools[exposed] = (session, original)


def test_unknown_tool_returns_an_error_string(manager):
    assert manager.call("nope", {}) == "Error: unknown tool 'nope'"


def test_call_uses_the_servers_own_tool_name_and_joins_text_parts(manager):
    s = FakeSession(SimpleNamespace(content=[_text("a"), _text("b")], isError=False))
    _register(manager, s, exposed="srv__t", original="t")
    assert manager.call("srv__t", {"q": 1}) == "a\nb"
    assert s.seen == [("t", {"q": 1})]


def test_non_text_content_is_omitted_and_empty_output_is_labelled(manager):
    _register(manager, FakeSession(SimpleNamespace(content=[_text("x"), SimpleNamespace(type="image")], isError=False)))
    assert manager.call("t", {}) == "x\n[image content omitted]"
    _register(manager, FakeSession(SimpleNamespace(content=[], isError=False)))
    assert manager.call("t", {}) == "(no output)"


@pytest.mark.parametrize("flag", ["isError", "is_error"])
def test_tool_reported_errors_are_prefixed_under_either_sdk_spelling(manager, flag):
    _register(manager, FakeSession(SimpleNamespace(content=[_text("bad input")], **{flag: True})))
    assert manager.call("t", {}) == "Error: bad input"


def test_long_output_is_truncated(manager, monkeypatch):
    monkeypatch.setattr(mcp_client, "MAX_TOOL_RESULT_CHARS", 10)
    _register(manager, FakeSession(SimpleNamespace(content=[_text("x" * 50)], isError=False)))
    assert manager.call("t", {}) == "x" * 10 + "\n…[truncated]"


def test_call_never_raises_it_returns_the_failure_as_text(manager):
    _register(manager, FakeSession(exc=RuntimeError("boom")))
    assert manager.call("t", {}) == "Error: RuntimeError: boom"


def test_slow_tool_times_out(manager, monkeypatch):
    monkeypatch.setattr(mcp_client, "TOOL_TIMEOUT", 0.05)
    _register(manager, FakeSession(SimpleNamespace(content=[], isError=False), delay=2))
    assert "timed out after 0.05s" in manager.call("t", {})


# ── start / close with real subprocesses ──────────────────────────────────────

@pytest.fixture
def real_manager():
    m = McpManager()
    yield m
    m.close()


def test_a_server_that_cannot_start_is_reported_not_raised(real_manager):
    bad = McpServerConfig(name="broken", command="definitely-not-a-real-command-xyz")
    assert real_manager.start([bad]) is True
    assert "broken" in real_manager.errors
    assert real_manager.tool_names == [] and real_manager.openai_tools() == []


@pytest.mark.skipif(not mcp_client.MCP_AVAILABLE, reason="mcp package not installed")
def test_connects_to_the_bundled_brave_server_and_exposes_openai_tool_schemas(real_manager):
    srv = McpServerConfig(name="brave", command=sys.executable,
                          args=[str(ROOT / "brave_search_mcp.py")], env={"BRAVE_API_KEY": "dummy"})
    assert real_manager.start([srv]) is True
    assert real_manager.errors == {}
    assert real_manager.tool_names == ["brave_web_search", "brave_news_search"]
    assert real_manager.server_tools == {"brave": ["brave_web_search", "brave_news_search"]}

    web = next(t for t in real_manager.openai_tools() if t["function"]["name"] == "brave_web_search")
    params = web["function"]["parameters"]
    assert web["type"] == "function" and "query" in params["properties"]
    assert "title" not in params and params["properties"]["country"]["type"] == "string"  # Optional[str] collapsed

    assert real_manager.start([srv]) is False    # same server set: nothing restarted
    real_manager.close()
    assert real_manager.tool_names == []
    real_manager.close()                         # closing twice is harmless
