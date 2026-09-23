"""MCP servers menu: command/path, arguments and env vars, plus connecting them."""

import os
import shlex
import getpass
from typing import Dict, Optional, Sequence

from cryptography.fernet import Fernet
from rich.markup import escape

from rich.prompt import Prompt

from ...mcp_client import MCP_AVAILABLE, McpManager, safe_name
from ...models import Config, McpServerConfig
from ...paths import LOG_DIR
from ...store import save_config
from ..common import console, pick
from .base import CollectionMenu, MenuAction, run_collection_menu


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
