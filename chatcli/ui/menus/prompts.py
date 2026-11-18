"""System prompts menu: multi-line saved prompt text."""

from cryptography.fernet import Fernet
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import HTML
from rich.prompt import Prompt

from ...models import Config, SystemPrompt
from ...store import save_config
from ..common import console, pick
from .base import CollectionMenu, MenuAction, run_collection_menu


def _read_lines() -> str:
    """Read multi-line input until a line containing only '---'."""
    console.print("[dim]Enter prompt text. Finish with a line containing only '---'[/dim]")
    lines = []
    while True:
        line = input()
        if line.strip() == "---":
            break
        lines.append(line)
    return "\n".join(lines)


def _new_prompt() -> SystemPrompt:
    name = Prompt.ask("Name (e.g. Coding Assistant)")
    return SystemPrompt(name=name, content=_read_lines())


def _edit_prompt(config: Config, fernet: Fernet) -> None:
    """Pick an existing prompt and let the user update its name and/or content."""
    idx = pick("Edit prompt", config.system_prompts, lambda p: p.name)
    if idx is None:
        return
    p = config.system_prompts[idx]
    new_name = Prompt.ask("Name", default=p.name)
    console.print(f"[dim]Current content:[/dim]\n{p.content}")
    keep = Prompt.ask("Keep existing content?", choices=["y", "n"], default="y")
    if keep == "n":
        console.print("[dim]Enter or paste prompt text:[/dim]")
        new_content = pt_prompt(HTML("<b><ansicyan>Content</ansicyan></b>: ")).strip()
        if new_content:
            p.content = new_content
    p.name = new_name
    save_config(config, fernet)
    console.print(f"[green]Prompt '{p.name}' updated.[/green]")


def _prompt_row(p: SystemPrompt) -> tuple:
    preview = p.content[:80].replace("\n", " ")
    if len(p.content) > 80:
        preview += "…"
    return p.name, preview


PROMPTS_MENU = CollectionMenu(
    title="System Prompts",
    attr="system_prompts",
    label=lambda p: p.name,
    delete_title="Delete prompt",
    empty_message="No system prompts saved yet.",
    columns=("Name", "Preview"),
    row=_prompt_row,
    prompt_new=_new_prompt,
    saved_message=lambda p: f"Prompt '{p.name}' saved.",
    extras=(MenuAction("Edit", _edit_prompt),),
)


def manage_prompts(config: Config, fernet: Fernet) -> None:
    run_collection_menu(PROMPTS_MENU, config, fernet)
