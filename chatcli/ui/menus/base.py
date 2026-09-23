"""The generic Add/Delete/List engine shared by every managed collection."""

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from cryptography.fernet import Fernet
from rich.prompt import Prompt
from rich.table import Table

from ...models import Config
from ...store import save_config
from ..common import console, divider, pick


@dataclass
class MenuAction:
    """An extra numbered entry, shown after Add, Delete and List."""
    label: str
    run: Callable[[Config, Fernet], None]


@dataclass
class CollectionMenu:
    """Describes a saved list (endpoints, prompts, ...) so one loop can manage any of them."""
    title: str                              # shown as the divider and as the list table's title
    attr: str                               # name of the Config field holding the list
    label: Callable[[Any], str]             # one-line description used in pickers
    delete_title: str
    empty_message: str
    columns: Sequence[str]
    row: Callable[[Any], Sequence[str]]     # one table row per item, matching `columns`
    prompt_new: Callable[[], Any]           # asks the user for a new item and returns it
    saved_message: Callable[[Any], str]
    extras: Sequence[MenuAction] = ()


def run_collection_menu(menu: CollectionMenu, config: Config, fernet: Fernet) -> None:
    items = getattr(config, menu.attr)
    choices = ["Add", "Delete", "List"] + [a.label for a in menu.extras]
    header = (
        "  " + "   ".join(f"[yellow]{i}[/yellow]. {text}" for i, text in enumerate(choices, 1))
        + "   [yellow]0[/yellow]. Back"
    )
    while True:
        divider(menu.title)
        console.print(header)
        ch = Prompt.ask("›", default="0")

        if ch == "0":
            return

        elif ch == "1":
            item = menu.prompt_new()
            items.append(item)
            save_config(config, fernet)
            console.print(f"[green]{menu.saved_message(item)}[/green]")

        elif ch == "2":
            idx = pick(menu.delete_title, items, menu.label)
            if idx is not None:
                removed = items.pop(idx)
                save_config(config, fernet)
                console.print(f"[green]Deleted '{removed.name}'.[/green]")

        elif ch == "3":
            if not items:
                console.print(f"[yellow]{menu.empty_message}[/yellow]")
                continue
            t = Table(title=menu.title, show_lines=True)
            for col in menu.columns:
                t.add_column(col)
            for item in items:
                t.add_row(*menu.row(item))
            console.print(t)

        elif ch.isdigit() and 4 <= int(ch) < 4 + len(menu.extras):
            menu.extras[int(ch) - 4].run(config, fernet)
