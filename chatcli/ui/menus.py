"""Management menus: endpoints, system prompts, settings and MCP servers."""

import os
import sys
import shlex
import getpass
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from cryptography.fernet import Fernet
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table

from ..mcp_client import MCP_AVAILABLE, McpManager, safe_name
from ..models import Config, Endpoint, McpServerConfig, SystemPrompt
from ..paths import CONFIG_FILE, LOG_DIR
from ..providers import EFFORT_LEVELS, reasoning_label
from ..store import ExportError, export_config, import_config, save_config
from .common import console, divider, pick


# ── Generic Add / Delete / List menu ──────────────────────────────────────────

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


# ── Endpoints ─────────────────────────────────────────────────────────────────

def ep_label(e: Endpoint) -> str:
    return f"{e.name}  [{e.model}]  {e.base_url}"


def _ask_thinking(cur_thinking: bool = True, cur_effort: str = "default") -> tuple:
    """Prompt for thinking on/off and reasoning effort. Returns (thinking, effort)."""
    thinking = Prompt.ask(
        "Thinking (sends reasoning_effort=none when off)",
        choices=["on", "off"],
        default="on" if cur_thinking else "off",
    ) == "on"
    effort = "default"
    if thinking:
        effort = Prompt.ask(
            "Reasoning effort ('default' = don't send, model decides)",
            choices=[lvl for lvl in EFFORT_LEVELS if lvl != "none"],
            default=cur_effort if cur_effort in EFFORT_LEVELS and cur_effort != "none" else "default",
        )
    return thinking, effort


def _new_endpoint() -> Endpoint:
    name     = Prompt.ask("Name (e.g. OpenAI)")
    base_url = Prompt.ask("Base URL (e.g. https://api.openai.com/v1)")
    api_key  = getpass.getpass("API Key: ")
    model    = Prompt.ask("Model (e.g. gpt-4o)")
    thinking, effort = _ask_thinking()
    return Endpoint(
        name=name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        thinking=thinking,
        reasoning_effort=effort,
    )


def _change_reasoning(config: Config, fernet: Fernet) -> None:
    idx = pick("Change reasoning settings for", config.endpoints, ep_label)
    if idx is not None:
        ep = config.endpoints[idx]
        ep.thinking, ep.reasoning_effort = _ask_thinking(ep.thinking, ep.reasoning_effort)
        save_config(config, fernet)
        console.print(f"[green]Updated '{ep.name}'.[/green]")


ENDPOINTS_MENU = CollectionMenu(
    title="Endpoints",
    attr="endpoints",
    label=ep_label,
    delete_title="Delete endpoint",
    empty_message="No endpoints saved yet.",
    columns=("Name", "Base URL", "Model", "Reasoning"),
    row=lambda e: (e.name, e.base_url, e.model, reasoning_label(e.thinking, e.reasoning_effort)),
    prompt_new=_new_endpoint,
    saved_message=lambda e: f"Endpoint '{e.name}' saved.",
    extras=(MenuAction("Reasoning settings", _change_reasoning),),
)


def manage_endpoints(config: Config, fernet: Fernet) -> None:
    run_collection_menu(ENDPOINTS_MENU, config, fernet)


# ── System prompts ────────────────────────────────────────────────────────────

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


# ── MCP servers ───────────────────────────────────────────────────────────────

def _mcp_label(s: McpServerConfig) -> str:
    return f"{s.name}  {s.command} {' '.join(s.args)}"


def _ask_command_and_args(cur_command: str = "", cur_args: Sequence[str] = ()) -> tuple:
    """Prompt for command and args, pre-filled with current values when editing."""
    command = Prompt.ask("Command (e.g. python)", default=cur_command or None)
    raw_args = Prompt.ask(
        "Arguments (e.g. C:\\path\\to\\brave_search_mcp.py)",
        default=" ".join(cur_args) if cur_args else "",
    )
    args = [a.strip("\"'") for a in shlex.split(raw_args, posix=(os.name != "nt"))]
    return command, args


def _ask_env(cur_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Prompt for environment variables, keeping existing ones by default."""
    if cur_env:
        keep = Prompt.ask(
            f"Keep existing env vars ({', '.join(cur_env)})?",
            choices=["y", "n"],
            default="y",
        )
        if keep == "y":
            return dict(cur_env)
    env: Dict[str, str] = {}
    console.print("[dim]Environment variables for the server (e.g. BRAVE_API_KEY). "
                  "Leave the name blank to finish.[/dim]")
    while True:
        key = Prompt.ask("Env var name", default="")
        if not key:
            break
        env[key] = getpass.getpass(f"{key} value: ")
    return env


def _new_mcp_server() -> McpServerConfig:
    name = Prompt.ask("Name (e.g. brave-search)")
    command, args = _ask_command_and_args()
    env = _ask_env()
    return McpServerConfig(name=name, command=command, args=args, env=env)


def _edit_mcp_server(config: Config, fernet: Fernet) -> None:
    idx = pick("Edit MCP server", config.mcp_servers, _mcp_label)
    if idx is None:
        return
    s = config.mcp_servers[idx]
    s.command, s.args = _ask_command_and_args(s.command, s.args)
    s.env = _ask_env(s.env)
    save_config(config, fernet)
    console.print(f"[green]MCP server '{s.name}' updated.[/green]")


def _mcp_menu(mcp: McpManager) -> CollectionMenu:
    def row(s: McpServerConfig) -> tuple:
        if s.name in mcp.errors:
            status = f"[red]failed: {escape(mcp.errors[s.name])}[/red]"
        elif s.name in mcp.server_tools:
            status = f"connected: {', '.join(mcp.server_tools[s.name]) or 'no tools'}"
        else:
            status = "[dim]not connected yet[/dim]"
        return s.name, escape(f"{s.command} {' '.join(s.args)}"), ", ".join(s.env) or "-", status

    return CollectionMenu(
        title="MCP Servers",
        attr="mcp_servers",
        label=_mcp_label,
        delete_title="Delete MCP server",
        empty_message="No MCP servers saved yet.",
        columns=("Name", "Command", "Env vars", "Status"),
        row=row,
        prompt_new=_new_mcp_server,
        saved_message=lambda s: f"MCP server '{s.name}' saved. It connects when you start a chat.",
        extras=(MenuAction("Edit server", _edit_mcp_server),),
    )


def manage_mcp_servers(config: Config, fernet: Fernet, mcp: McpManager) -> None:
    run_collection_menu(_mcp_menu(mcp), config, fernet)


def prepare_mcp(config: Config, mcp: McpManager) -> None:
    """Make sure the configured MCP servers are connected before a chat starts."""
    servers = config.mcp_servers
    if not servers:
        return
    if not MCP_AVAILABLE:
        console.print("[yellow]MCP servers are configured but the 'mcp' package isn't installed "
                      "(pip install mcp). Tools disabled.[/yellow]")
        return
    with console.status("[blue]Connecting MCP servers…[/blue]", spinner="dots"):
        started = mcp.start(servers)
    if started:
        for name, err in mcp.errors.items():
            console.print(
                f"[yellow]MCP server '{escape(name)}' failed to start: {escape(err)}[/yellow]\n"
                f"[dim]  server log: {LOG_DIR / ('mcp_' + safe_name(name) + '.log')}[/dim]"
            )


# ── Global settings ───────────────────────────────────────────────────────────

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
