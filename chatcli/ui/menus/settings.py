"""Global settings menu: usage-stats default plus config export/import."""

import sys
from pathlib import Path

from cryptography.fernet import Fernet
from rich.markup import escape
from rich.prompt import Prompt

from ...models import Config
from ...paths import CONFIG_FILE
from ...store import ExportError, export_config, import_config, save_config
from ..common import console, divider


def _export_config(config: Config, fernet: Fernet) -> None:
    dest = Prompt.ask("Export to file", default=str(Path.home() / "chatcli_export.enc"))
    try:
        export_config(Path(dest).expanduser())
    except FileNotFoundError as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]")
        return
    console.print(
        f"[green]Exported to {escape(dest)}.[/green] "
        "[dim]It's still encrypted with your master password. Copy it to the other "
        "machine and use 'Import config' there.[/dim]"
    )


def _import_config(config: Config, fernet: Fernet) -> None:
    src = Prompt.ask("Import from file")
    path = Path(src).expanduser()
    if not path.exists():
        console.print("[red]File not found.[/red]")
        return
    if CONFIG_FILE.exists():
        confirm = Prompt.ask(
            "This replaces all saved endpoints, prompts, MCP servers and settings on this "
            "machine with the imported ones",
            choices=["y", "n"],
            default="n",
        )
        if confirm != "y":
            return
    try:
        import_config(path)
    except ExportError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return
    console.print(
        "[green]Config imported.[/green] [dim]Restart ChatCLI and enter the master "
        "password used on the machine that made the export.[/dim]"
    )
    sys.exit(0)


def manage_settings(config: Config, fernet: Fernet) -> None:
    while True:
        divider("Settings")
        usage_on = config.settings.show_usage
        console.print(
            f"  [yellow]1[/yellow]. Show token/cache usage after each reply, by default: "
            f"[bold]{'on' if usage_on else 'off'}[/bold]  [dim](select to toggle)[/dim]\n"
            "  [yellow]2[/yellow]. Export config to file\n"
            "  [yellow]3[/yellow]. Import config from file\n"
            "  [yellow]0[/yellow]. Back"
        )
        ch = Prompt.ask("›", default="0")

        if ch == "0":
            return

        elif ch == "1":
            config.settings.show_usage = not usage_on
            save_config(config, fernet)
            console.print(
                f"[green]Usage stats default: {'on' if not usage_on else 'off'}.[/green] "
                "[dim]Applies to new conversations, and to old ones that never used /usage. "
                "/usage in a chat still overrides it for that conversation.[/dim]"
            )

        elif ch == "2":
            _export_config(config, fernet)

        elif ch == "3":
            _import_config(config, fernet)
