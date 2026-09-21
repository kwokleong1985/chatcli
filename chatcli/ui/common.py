"""Shared terminal UI helpers."""

from typing import Optional

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

console = Console()


def pick(title: str, items: list, label_fn=str) -> Optional[int]:
    """Numbered selection menu. Returns index or None if user cancels."""
    if not items:
        return None
    console.print(f"\n[bold cyan]{title}[/bold cyan]")
    for i, item in enumerate(items, 1):
        console.print(f"  [yellow]{i}[/yellow]. {escape(label_fn(item))}")   # labels hold user text, e.g. [model]
    console.print("  [dim]0. Back[/dim]")
    while True:
        raw = Prompt.ask("›", default="0")
        if raw.isdigit() and 0 <= int(raw) <= len(items):
            n = int(raw)
            return n - 1 if n > 0 else None
        console.print("[red]Enter a number from the list.[/red]")


def divider(text: str = "") -> None:
    console.rule(f"[bold cyan]{text}[/bold cyan]" if text else "")
