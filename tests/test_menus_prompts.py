import builtins

from chatcli import store
from chatcli.models import Config, SystemPrompt
from chatcli.ui.menus import prompts as prompts_menu


def flat(buf) -> str:
    return " ".join(buf.getvalue().replace("│", " ").split())


def reload(fernet) -> Config:
    return store.load_config(fernet)


def test_add_prompt_reads_lines_until_the_terminator(fernet, out, prompts, monkeypatch):
    lines = iter(["line one", "", "line three", "---"])
    monkeypatch.setattr(builtins, "input", lambda *_: next(lines))
    prompts("1", "Coder", "0")
    prompts_menu.manage_prompts(Config(), fernet)
    (p,) = reload(fernet).system_prompts
    assert p == SystemPrompt("Coder", "line one\n\nline three")


def test_prompt_list_shows_a_single_line_preview_cut_at_80_chars(fernet, out, prompts):
    cfg = Config(system_prompts=[SystemPrompt("short", "a\nb"), SystemPrompt("long", "x" * 100)])
    prompts("3", "0")
    prompts_menu.manage_prompts(cfg, fernet)
    text = flat(out)
    assert "short a b" in text and "x" * 80 + "…" in text and "x" * 81 not in text


def test_delete_prompt(fernet, out, prompts, system_prompt):
    prompts("2", "1", "0")
    cfg = Config(system_prompts=[system_prompt])
    prompts_menu.manage_prompts(cfg, fernet)
    assert reload(fernet).system_prompts == []


# ── Edit tests ────────────────────────────────────────────────────────────────

def test_edit_prompt_rename_and_keep_content(fernet, out, prompts, system_prompt):
    """Edit action (choice 4): rename without changing content."""
    cfg = Config(system_prompts=[system_prompt])
    # Menu: 4=Edit, pick item 1, new name "Updated", keep content y, then 0=Back
    prompts("4", "1", "Updated", "y", "0")
    prompts_menu.manage_prompts(cfg, fernet)
    assert cfg.system_prompts[0].name == "Updated"
    assert cfg.system_prompts[0].content == system_prompt.content
    # Persisted
    (saved,) = reload(fernet).system_prompts
    assert saved.name == "Updated"
    assert saved.content == system_prompt.content


def test_edit_prompt_replace_content(fernet, out, prompts, system_prompt):
    """Edit action (choice 4): change both name and content with multiline paste."""
    cfg = Config(system_prompts=[system_prompt])
    # Menu: 4=Edit, pick item 1, same name, replace content n, pasted multiline content, then 0=Back
    prompts("4", "1", system_prompt.name, "n", "new line 1\nnew line 2", "0")
    prompts_menu.manage_prompts(cfg, fernet)
    assert cfg.system_prompts[0].content == "new line 1\nnew line 2"
    (saved,) = reload(fernet).system_prompts
    assert saved.content == "new line 1\nnew line 2"


def test_edit_prompt_cancel_pick_returns_to_menu(fernet, out, prompts, system_prompt):
    """Edit action (choice 4): cancelling the picker (pick 0) returns without saving."""
    cfg = Config(system_prompts=[system_prompt])
    original_content = system_prompt.content
    # Menu: 4=Edit, pick 0 (cancel), then 0=Back
    prompts("4", "0", "0")
    prompts_menu.manage_prompts(cfg, fernet)
    # Nothing changed and nothing saved
    assert cfg.system_prompts[0].content == original_content
