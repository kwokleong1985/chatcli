"""System prompts menu: multi-line saved prompt text."""

from cryptography.fernet import Fernet
from rich.prompt import Prompt

from ...models import Config, SystemPrompt
from ..common import console
from .base import CollectionMenu, run_collection_menu


def _new_prompt() -> SystemPrompt:
    name = Prompt.ask("Name (e.g. Coding Assistant)")
    console.print("[dim]Enter prompt text. Finish with a line containing only '---'[/dim]")
    lines = []
    while True:
        line = input()
        if line.strip() == "---":
            break
        lines.append(line)
    return SystemPrompt(name=name, content="\n".join(lines))


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
)


def manage_prompts(config: Config, fernet: Fernet) -> None:
    run_collection_menu(PROMPTS_MENU, config, fernet)
