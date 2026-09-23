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
