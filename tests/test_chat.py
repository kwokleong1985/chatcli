from types import SimpleNamespace

import pytest

from chatcli import paths, store
from chatcli.providers import EFFORT_LEVELS
from chatcli.ui import chat
from chatcli.ui.chat import ChatContext, chat_loop


class FakeMcp:
    def __init__(self, server_tools=None):
        self.server_tools = server_tools if server_tools is not None else {"srv": ["t1", "t2"]}
        self.tool_names = [t for names in self.server_tools.values() for t in names]


def flat(buf) -> str:
    """Captured output with rich's line wrapping and box borders collapsed to single spaces."""
    return " ".join(buf.getvalue().replace("│", " ").split())


def saved(session):
    (s,) = [x for x in store.list_sessions() if x.path == session.path]
    return s


@pytest.fixture
def ctx(session):
    return ChatContext(session, FakeMcp())


def run(name, ctx, arg=""):
    chat._BY_NAME[name].run(ctx, arg)


# ── registry ──────────────────────────────────────────────────────────────────

def test_help_line_lists_every_command_in_the_expected_order():
    assert chat._HELP == (
        "[dim]/info[/dim]  show session details    "
        "[dim]/quit[/dim]  exit    "
        "[dim]/system[/dim]  show system prompt    "
        "[dim]/thinking on|off[/dim]  toggle thinking    "
        "[dim]/effort <level>[/dim]  set reasoning effort    "
        "[dim]/usage on|off[/dim]  show token/cache stats per reply    "
        "[dim]/tools \\[on|off][/dim]  list / toggle MCP tools    "
        "[dim]/clear[/dim]  delete chat history (keeps settings)"
    )


def test_command_names_are_unique_and_aliases_share_a_handler():
    all_names = [n for c in chat.COMMANDS for n in c.names]
    assert len(all_names) == len(set(all_names))
    assert chat._BY_NAME["/exit"] is chat._BY_NAME["/q"] is chat._BY_NAME["/quit"]


# ── individual commands ───────────────────────────────────────────────────────

def test_info_shows_the_session_details(ctx, out):
    ctx.sess.messages = [{"role": "user", "content": "x"}]
    run("/info", ctx)
    text = flat(out)
    for part in ("Session: chat", "Endpoint: ep", "Model: m", "Prompt: P", "Thinking: on (default)",
                 "Usage stats: off", "Tools: 2 on", "Messages: 1"):
        assert part in text


def test_quit_stops_the_loop(ctx):
    run("/quit", ctx)
    assert ctx.running is False


def test_system_shows_the_prompt(ctx, out):
    run("/system", ctx)
    assert "be brief" in flat(out) and "System Prompt — P" in flat(out)


@pytest.mark.parametrize("arg, expected", [("off", False), ("on", True)])
def test_thinking_toggle_is_saved(ctx, out, arg, expected):
    ctx.sess.thinking = not expected
    run("/thinking", ctx, arg)
    assert ctx.sess.thinking is expected and saved(ctx.sess).thinking is expected


@pytest.mark.parametrize("name, arg", [("/thinking", ""), ("/thinking", "maybe"), ("/usage", ""), ("/usage", "1")])
def test_on_off_commands_reject_bad_arguments_without_saving(ctx, out, name, arg):
    run(name, ctx, arg)
    assert f"Usage: {name} on|off" in flat(out)
    assert not ctx.sess.path.exists()


def test_effort_sets_level_and_turns_thinking_on(ctx, out):
    ctx.sess.thinking = False
    run("/effort", ctx, "high")
    s = saved(ctx.sess)
    assert (s.thinking, s.reasoning_effort) == (True, "high")
    assert "Thinking: on (high)" in flat(out)


def test_effort_none_means_thinking_off_and_keeps_the_previous_level(ctx, out):
    ctx.sess.reasoning_effort = "low"
    run("/effort", ctx, "none")
    s = saved(ctx.sess)
    assert (s.thinking, s.reasoning_effort) == (False, "low")


def test_effort_works_on_a_session_resumed_from_an_old_history_file(out):
    # Regression: these files had no reasoning_effort and /effort none used to raise KeyError.
    from chatcli.models import Session
    old = Session.from_dict({"name": "n", "endpoint_name": "e", "base_url": "u", "model": "m",
                             "prompt_name": "p", "system_prompt": "s", "thinking": True})
    old.path = paths.HISTORY_DIR / "old.json"
    run("/effort", ChatContext(old, None), "none")
    assert old.thinking is False


def test_effort_rejects_unknown_levels_and_lists_the_valid_ones(ctx, out):
    run("/effort", ctx, "bogus")
    assert "Usage: /effort " + "|".join(EFFORT_LEVELS) in flat(out)
    assert not ctx.sess.path.exists()


def test_usage_toggle_is_saved(ctx, out):
    run("/usage", ctx, "on")
    assert saved(ctx.sess).show_usage is True and "Usage stats: on" in flat(out)


def test_tools_on_off_is_saved_and_reflected_in_the_label(ctx, out):
    run("/tools", ctx, "off")
    assert saved(ctx.sess).tools is False and "Tools: 2 off" in flat(out)
    run("/tools", ctx, "on")
    assert saved(ctx.sess).tools is True


def test_tools_without_an_argument_lists_tools_per_server(ctx, out):
    run("/tools", ctx)
    assert "srv: t1, t2" in flat(out)


def test_tools_with_nothing_connected_says_how_to_add_a_server(session, out):
    run("/tools", ChatContext(session, None))
    run("/tools", ChatContext(session, FakeMcp(server_tools={})))
    assert flat(out).count("No MCP tools available") == 2


def test_tools_rejects_a_bad_argument(ctx, out):
    run("/tools", ctx, "maybe")
    assert "Usage: /tools [on|off]" in flat(out)


def test_clear_on_an_empty_history_does_nothing(ctx, out, prompts):
    prompts()   # would fail if it asked for confirmation
    run("/clear", ctx)
    assert "History is already empty" in flat(out)


def test_clear_asks_first_and_keeps_history_when_declined(ctx, out, prompts):
    ctx.sess.messages = [{"role": "user", "content": "x"}]
    prompts("n")
    run("/clear", ctx)
    assert len(ctx.sess.messages) == 1 and not ctx.sess.path.exists()


def test_clear_confirmed_wipes_messages_but_keeps_settings(ctx, out, prompts):
    ctx.sess.messages = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    ctx.sess.reasoning_effort = "high"
    prompts("y")
    run("/clear", ctx)
    s = saved(ctx.sess)
    assert s.messages == [] and s.reasoning_effort == "high" and s.endpoint_name == "ep"
    assert "Chat history cleared." in flat(out) and "Chat — chat" in flat(out)


# ── the loop ──────────────────────────────────────────────────────────────────

@pytest.fixture
def model(monkeypatch):
    """Replace the API call; `model.sent` records what would have been sent."""
    state = SimpleNamespace(sent=[], reply="the reply", usage=None, error=None)

    def fake(sess, text, mcp, on_tool):
        state.sent.append(text)
        if state.error:
            raise state.error
        return state.reply, state.usage
    monkeypatch.setattr(chat, "ask_model", fake)
    return state


def test_a_turn_is_shown_and_saved(session, out, prompts, model):
    prompts("hello", "/quit")
    chat_loop(session, None)
    assert model.sent == ["hello"]
    assert saved(session).messages == [{"role": "user", "content": "hello"},
                                       {"role": "assistant", "content": "the reply"}]
    text = flat(out)
    assert "hello" in text and "the reply" in text and "Conversation saved." in text
    assert "You" in text and "Assistant" in text


def test_usage_line_appears_only_when_enabled(session, out, prompts, model):
    model.usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    prompts("one", "/usage on", "two", "/quit")
    chat_loop(session, None)
    assert flat(out).count("usage: prompt 10") == 1


def test_a_failed_turn_is_reported_and_not_saved(session, out, prompts, model):
    model.error = RuntimeError("upstream down")
    prompts("hello", "/quit")
    chat_loop(session, None)
    text = flat(out)
    assert "API error: upstream down" in text and "logged to" in text
    assert session.messages == [] and not session.path.exists()


def test_the_conversation_carries_on_after_a_failed_turn(session, out, prompts, model):
    model.error = RuntimeError("blip")
    prompts("first", "/quit")
    chat_loop(session, None)
    model.error = None
    prompts("second", "/quit")
    chat_loop(session, None)
    assert [m["content"] for m in saved(session).messages] == ["second", "the reply"]


def test_commands_are_case_insensitive_and_ignore_extra_words(session, out, prompts, model):
    prompts("/INFO", "/Info please", "/QUIT now")
    chat_loop(session, None)
    assert model.sent == [] and flat(out).count("Session: chat") == 2


@pytest.mark.parametrize("text", ["/etc/hosts what is this file", "/unknown", "/toolsx"])
def test_text_that_is_not_a_command_goes_to_the_model(session, out, prompts, model, text):
    prompts(text, "/quit")
    chat_loop(session, None)
    assert model.sent == [text]


def test_blank_input_is_ignored(session, out, prompts, model):
    prompts("", "   ", "/quit")
    chat_loop(session, None)
    assert model.sent == []


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), EOFError()])
def test_ctrl_c_and_eof_end_the_chat_cleanly(session, out, prompts, model, exc):
    prompts(exc)
    chat_loop(session, None)
    assert "Conversation saved." in flat(out)


def test_resuming_replays_the_saved_messages(session, out, prompts, model):
    session.messages = [{"role": "user", "content": "old question"},
                        {"role": "assistant", "content": "old **answer**"}]
    prompts("/quit")
    chat_loop(session, None)
    text = flat(out)
    assert "old question" in text and "old answer" in text and "**" not in text   # markdown was rendered
    assert model.sent == []


def test_banner_shows_session_and_the_help_line(session, out, prompts, model):
    prompts("/quit")
    chat_loop(session, FakeMcp())
    text = flat(out)
    assert "Chat — chat" in text and "ep · m · prompt: P · thinking: on (default) · tools: 2 on" in text
    assert "/clear delete chat history (keeps settings)" in text


def test_tool_calls_are_announced_as_they_happen(out):
    chat._show_tool_call("search", {"q": "é"}, "12345")
    chat._show_tool_call("search", {}, "Error: nope")
    text = flat(out)
    assert 'search({"q": "é"}) -> 5 chars' in text and "Error: nope" in text


# ── text that looks like rich markup must be shown, never interpreted ─────────
# Regression: "[on|off]" hints were swallowed as a style tag, and a stray "[/word]" in
# anything the user typed (or in a provider error) raised MarkupError and killed the app.

def test_the_bracketed_argument_hints_are_visible(session, out, prompts, model):
    prompts("/tools maybe", "/quit")
    chat_loop(session, FakeMcp())
    text = flat(out)
    assert "/tools [on|off] list / toggle MCP tools" in text    # help line
    assert "Usage: /tools [on|off]" in text                       # usage error


def test_a_message_containing_a_stray_closing_tag_is_sent_and_shown_intact(session, out, prompts, model):
    prompts("why does [/bold] break things? see [note] and [1]", "/quit")
    chat_loop(session, None)
    assert model.sent == ["why does [/bold] break things? see [note] and [1]"]
    assert "why does [/bold] break things? see [note] and [1]" in flat(out)
    assert saved(session).messages[0]["content"].startswith("why does [/bold]")


def test_a_provider_error_containing_a_stray_closing_tag_is_reported_and_the_chat_goes_on(session, out, prompts, model):
    model.error = RuntimeError("bad payload [/json] near [type=missing]")
    prompts("hello", "second try", "/quit")
    chat_loop(session, None)
    assert "API error: bad payload [/json] near [type=missing]" in flat(out)
    assert model.sent == ["hello", "second try"]


def test_a_system_prompt_with_markup_like_text_is_shown_literally(ctx, out):
    ctx.sess.system_prompt = "Use [bold]tags[/bold] and [/oops] literally"
    run("/system", ctx)
    assert "Use [bold]tags[/bold] and [/oops] literally" in flat(out)
