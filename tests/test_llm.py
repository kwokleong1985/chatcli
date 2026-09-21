import copy
import json
from types import SimpleNamespace

import pytest

from chatcli import llm, paths
from chatcli.llm import MAX_TOOL_ROUNDS, _add_usage, ask_model, log_api_error

GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"


# ── fakes ─────────────────────────────────────────────────────────────────────

def _usage(prompt, completion, cached=0):
    data = {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion, "prompt_tokens_details": {"cached_tokens": cached}}
    return SimpleNamespace(model_dump=lambda: data)


def text_reply(content, usage=None):
    msg = SimpleNamespace(tool_calls=None, content=content)
    return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=msg)])


def tool_reply(*calls, usage=None):
    """calls: (id, name, raw_json_arguments)"""
    tcs = [SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=a)) for i, n, a in calls]
    dump = {"role": "assistant", "tool_calls": [
        {"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in calls]}
    msg = SimpleNamespace(tool_calls=tcs, content=None, model_dump=lambda exclude_none=True: dump)
    return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=msg)])


class FakeClient:
    """Stands in for openai.OpenAI. `replies` are returned in order (the last one repeats)."""

    def __init__(self, *replies):
        self.replies, self.calls, self.init_kwargs = list(replies), [], None
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))   # history is mutated after the call, so snapshot it
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def client(monkeypatch):
    def install(*replies):
        c = FakeClient(*replies)

        def factory(**kwargs):
            c.init_kwargs = kwargs
            return c
        monkeypatch.setattr(llm, "OpenAI", factory)
        return c
    return install


class FakeMcp:
    def __init__(self):
        self.calls = []

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "t1", "parameters": {}}}]

    def call(self, name, args):
        self.calls.append((name, args))
        return f"result:{name}"


# ── plain replies ─────────────────────────────────────────────────────────────

def test_plain_reply_returns_text_and_usage_and_builds_the_request(client, session):
    c = client(text_reply("hello", usage=_usage(10, 2)))
    session.messages = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "answer"}]
    reply, usage = ask_model(session, "now")

    assert reply == "hello" and usage["prompt_tokens"] == 10
    assert c.init_kwargs == {"api_key": "KEY", "base_url": "http://x/v1"}
    (call,) = c.calls
    assert call["model"] == "m"
    assert call["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "now"},
    ]
    assert "tools" not in call and "extra_body" not in call


def test_ask_model_does_not_touch_the_saved_history(client, session):
    client(text_reply("hi"))
    ask_model(session, "q")
    assert session.messages == []      # the caller decides whether to store the turn


def test_missing_content_and_usage_are_tolerated(client, session):
    client(text_reply(None, usage=None))
    assert ask_model(session, "q") == ("", None)


def test_reasoning_settings_go_out_via_extra_body(client, session):
    c = client(text_reply("x"))
    session.reasoning_effort = "high"
    ask_model(session, "q")
    assert c.calls[0]["extra_body"] == {"reasoning_effort": "high"}

    c = client(text_reply("x"))
    session.thinking = False
    ask_model(session, "q")
    assert c.calls[0]["extra_body"] == {"reasoning_effort": "none"}


# ── tool calling ──────────────────────────────────────────────────────────────

def test_tool_round_trip_feeds_the_result_back_and_sums_usage(client, session):
    c = client(
        tool_reply(("c1", "t1", '{"q": "x"}'), usage=_usage(10, 1, cached=4)),
        text_reply("final", usage=_usage(20, 2, cached=6)),
    )
    mcp, seen = FakeMcp(), []
    reply, usage = ask_model(session, "go", mcp, on_tool=lambda *a: seen.append(a))

    assert reply == "final"
    assert mcp.calls == [("t1", {"q": "x"})]
    assert seen == [("t1", {"q": "x"}, "result:t1")]
    assert usage["prompt_tokens"] == 30 and usage["prompt_tokens_details"]["cached_tokens"] == 10
    first, second = c.calls
    assert first["tools"] == mcp.openai_tools()
    assert second["messages"][-2]["tool_calls"][0]["id"] == "c1"
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "result:t1"}


def test_several_tool_calls_in_one_reply_are_all_answered(client, session):
    c = client(tool_reply(("a", "t1", "{}"), ("b", "t1", "{}")), text_reply("done"))
    mcp = FakeMcp()
    ask_model(session, "go", mcp)
    tool_msgs = [m for m in c.calls[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["a", "b"] and len(mcp.calls) == 2


def test_empty_arguments_mean_no_arguments(client, session):
    client(tool_reply(("c1", "t1", "")), text_reply("done"))
    mcp = FakeMcp()
    ask_model(session, "go", mcp)
    assert mcp.calls == [("t1", {})]


def test_malformed_tool_arguments_become_an_error_result_without_calling_the_tool(client, session):
    c = client(tool_reply(("c1", "t1", "{oops")), text_reply("recovered"))
    mcp, seen = FakeMcp(), []
    assert ask_model(session, "go", mcp, on_tool=lambda *a: seen.append(a))[0] == "recovered"
    assert mcp.calls == []
    assert seen == [("t1", {}, "Error: tool arguments were not valid JSON")]
    assert c.calls[1]["messages"][-1]["content"] == "Error: tool arguments were not valid JSON"


def test_tools_are_not_offered_when_switched_off_or_when_there_is_no_manager(client, session):
    c = client(text_reply("x"))
    session.tools = False
    ask_model(session, "q", FakeMcp())
    session.tools = True
    ask_model(session, "q", None)
    assert all("tools" not in call for call in c.calls)


def test_a_model_that_never_stops_calling_tools_is_cut_off(client, session):
    c = client(tool_reply(("c", "t1", "{}")))     # repeats forever
    reply, _ = ask_model(session, "go", FakeMcp())
    assert reply.startswith("(Stopped: too many tool-call rounds")
    assert len(c.calls) == MAX_TOOL_ROUNDS + 1
    assert "tools" in c.calls[MAX_TOOL_ROUNDS - 1] and "tools" not in c.calls[-1]   # last try forces text


# ── failures ──────────────────────────────────────────────────────────────────

def test_api_failure_is_logged_without_the_key_and_reraised(client, session):
    client(RuntimeError("upstream down"))
    with pytest.raises(RuntimeError, match="upstream down"):
        ask_model(session, "secret question")
    (line,) = paths.API_ERROR_LOG.read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert entry["endpoint"] == "ep" and entry["error_type"] == "RuntimeError"
    assert entry["request"]["messages"][-1]["content"] == "secret question"
    assert "KEY" not in line


def test_a_logging_failure_does_not_mask_the_real_api_error(client, session, monkeypatch):
    client(RuntimeError("upstream down"))

    def broken_log(*args, **kwargs):
        raise OSError("log disk full")
    monkeypatch.setattr(llm, "log_api_error", broken_log)
    with pytest.raises(RuntimeError, match="upstream down"):
        ask_model(session, "q")


def test_log_entry_captures_the_http_response_when_the_error_has_one(session):
    exc = RuntimeError("429")
    exc.response = SimpleNamespace(status_code=429, headers={"retry-after": "5"}, text="slow down")
    log_api_error(session, {"model": "m"}, exc)
    entry = json.loads(paths.API_ERROR_LOG.read_text(encoding="utf-8"))
    assert entry["response"] == {"status_code": 429, "headers": {"retry-after": "5"}, "body": "slow down"}
    assert entry["base_url"] == "http://x/v1"


def test_log_entries_append(session):
    log_api_error(session, {}, RuntimeError("one"))
    log_api_error(session, {}, RuntimeError("two"))
    assert len(paths.API_ERROR_LOG.read_text(encoding="utf-8").splitlines()) == 2


# ── _add_usage ────────────────────────────────────────────────────────────────

def test_add_usage_sums_numbers_and_recurses_into_nested_dicts():
    a = {"prompt_tokens": 1, "details": {"cached": 2}}
    b = {"prompt_tokens": 10, "details": {"cached": 20}, "extra": 5}
    assert _add_usage(a, b) == {"prompt_tokens": 11, "details": {"cached": 22}, "extra": 5}


def test_add_usage_handles_none_on_either_side():
    assert _add_usage(None, None) is None
    assert _add_usage(None, {"a": 1}) == {"a": 1}
    assert _add_usage({"a": 1}, None) == {"a": 1}
