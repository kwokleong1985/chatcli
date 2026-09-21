import pytest

from chatcli import paths, store
from chatcli.models import Config, Endpoint, Session, Settings, SystemPrompt
from chatcli.ui.sessions import resume_session, start_new


def make_config(**kw) -> Config:
    return Config(
        endpoints=[Endpoint("ep1", "http://a/v1", "KEY-A", "model-a", thinking=False, reasoning_effort="low"),
                   Endpoint("ep2", "http://b/v1", "KEY-B", "model-b")],
        system_prompts=[SystemPrompt("P1", "first"), SystemPrompt("P2", "second")],
        **kw,
    )


# ── start_new ─────────────────────────────────────────────────────────────────

def test_start_new_copies_endpoint_and_prompt_into_the_session(out, prompts):
    prompts("2", "2", "my chat")
    s = start_new(make_config(settings=Settings(show_usage=True)))
    assert (s.name, s.endpoint_name, s.base_url, s.api_key, s.model) == ("my chat", "ep2", "http://b/v1", "KEY-B", "model-b")
    assert (s.prompt_name, s.system_prompt) == ("P2", "second")
    assert s.show_usage is True and s.messages == []
    assert s.path.parent == paths.HISTORY_DIR and s.path.suffix == ".json"


def test_start_new_inherits_the_endpoints_reasoning_settings(out, prompts):
    prompts("1", "1", "n")
    s = start_new(make_config())
    assert (s.thinking, s.reasoning_effort) == (False, "low")


def test_start_new_uses_the_global_usage_default(out, prompts):
    prompts("1", "1", "n")
    assert start_new(make_config()).show_usage is False


def test_start_new_can_be_cancelled_at_either_picker(out, prompts):
    prompts("0")
    assert start_new(make_config()) is None
    prompts("1", "0")
    assert start_new(make_config()) is None


def test_start_new_does_not_write_anything_until_the_first_save(out, prompts):
    prompts("1", "1", "n")
    s = start_new(make_config())
    assert not s.path.exists()


# ── resume_session ────────────────────────────────────────────────────────────

def save_history(name="old", endpoint="ep1", **kw) -> Session:
    s = Session(name=name, endpoint_name=endpoint, base_url="http://stale/v1", model="stale-model",
                prompt_name="P1", system_prompt="sys", path=paths.HISTORY_DIR / f"{name}.json", **kw)
    store.save_session(s)
    return s


def test_resume_with_no_history_says_so(out, prompts):
    assert resume_session(make_config()) is None
    assert "No saved conversations found." in out.getvalue()


def test_resume_refreshes_credentials_from_the_encrypted_config(out, prompts):
    save_history(endpoint="ep2")
    prompts("1")
    s = resume_session(make_config())
    assert s.api_key == "KEY-B" and s.base_url == "http://b/v1"      # not the stale values on disk


def test_resume_can_be_cancelled(out, prompts):
    save_history()
    prompts("0")
    assert resume_session(make_config()) is None


def test_resume_of_a_deleted_endpoint_offers_a_replacement(out, prompts):
    save_history(endpoint="deleted-endpoint")
    prompts("1", "2")     # pick the conversation, then replacement endpoint ep2
    s = resume_session(make_config())
    assert (s.endpoint_name, s.model, s.api_key, s.base_url) == ("ep2", "model-b", "KEY-B", "http://b/v1")
    assert "no longer exists" in " ".join(out.getvalue().split())


def test_resume_of_a_deleted_endpoint_can_be_cancelled_at_the_replacement_picker(out, prompts):
    save_history(endpoint="deleted-endpoint")
    prompts("1", "0")
    assert resume_session(make_config()) is None


@pytest.mark.parametrize("global_default", [True, False])
def test_old_conversations_without_the_usage_setting_follow_the_global_default(out, prompts, global_default):
    save_history()                       # show_usage is None: never chose one
    prompts("1")
    s = resume_session(make_config(settings=Settings(show_usage=global_default)))
    assert s.show_usage is global_default


def test_an_explicit_per_conversation_usage_choice_beats_the_global_default(out, prompts):
    save_history(show_usage=False)
    prompts("1")
    assert resume_session(make_config(settings=Settings(show_usage=True))).show_usage is False


def test_the_list_shows_newest_first_with_message_counts(out, prompts):
    import os
    a = save_history("older"); b = save_history("newer")
    b.messages = [{"role": "user", "content": "x"}]; store.save_session(b)
    os.utime(a.path, (1_000_000_000, 1_000_000_000))
    prompts("0")
    resume_session(make_config())
    text = " ".join(out.getvalue().split())
    assert text.index("newer") < text.index("older") and "1 msgs" in text and "0 msgs" in text
