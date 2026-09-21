"""Management menus: endpoints, system prompts, settings and MCP servers."""

import os
import shlex
import getpass
from typing import Dict

from cryptography.fernet import Fernet
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table

from ..mcp_client import MCP_AVAILABLE, McpManager, safe_name
from ..models import Config, Endpoint, McpServerConfig, SystemPrompt
from ..paths import LOG_DIR
from ..providers import EFFORT_LEVELS, reasoning_label
from ..store import save_config
from .common import console, divider, pick


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


def manage_endpoints(config: Config, fernet: Fernet) -> None:
    while True:
        divider("Endpoints")
        console.print(
            "  [yellow]1[/yellow]. Add   "
            "[yellow]2[/yellow]. Delete   "
            "[yellow]3[/yellow]. List   "
            "[yellow]4[/yellow]. Reasoning settings   "
            "[yellow]0[/yellow]. Back"
        )
        ch = Prompt.ask("›", default="0")

        if ch == "0":
            return

        elif ch == "1":
            name     = Prompt.ask("Name (e.g. OpenAI)")
            base_url = Prompt.ask("Base URL (e.g. https://api.openai.com/v1)")
            api_key  = getpass.getpass("API Key: ")
            model    = Prompt.ask("Model (e.g. gpt-4o)")
            thinking, effort = _ask_thinking()
            config.endpoints.append(Endpoint(
                name=name,
                base_url=base_url,
                api_key=api_key,
                model=model,
                thinking=thinking,
                reasoning_effort=effort,
            ))
            save_config(config, fernet)
            console.print(f"[green]Endpoint '{name}' saved.[/green]")

        elif ch == "2":
            idx = pick("Delete endpoint", config.endpoints, ep_label)
            if idx is not None:
                removed = config.endpoints.pop(idx)
                save_config(config, fernet)
                console.print(f"[green]Deleted '{removed.name}'.[/green]")

        elif ch == "3":
            if not config.endpoints:
                console.print("[yellow]No endpoints saved yet.[/yellow]")
                continue
            t = Table(title="Endpoints", show_lines=True)
            for col in ("Name", "Base URL", "Model", "Reasoning"):
                t.add_column(col)
            for e in config.endpoints:
                t.add_row(e.name, e.base_url, e.model, reasoning_label(e.thinking, e.reasoning_effort))
            console.print(t)

        elif ch == "4":
            idx = pick("Change reasoning settings for", config.endpoints, ep_label)
            if idx is not None:
                ep = config.endpoints[idx]
                ep.thinking, ep.reasoning_effort = _ask_thinking(ep.thinking, ep.reasoning_effort)
                save_config(config, fernet)
                console.print(f"[green]Updated '{ep.name}'.[/green]")


# ── System prompt management ──────────────────────────────────────────────────

def manage_prompts(config: Config, fernet: Fernet) -> None:
    while True:
        divider("System Prompts")
        console.print(
            "  [yellow]1[/yellow]. Add   "
            "[yellow]2[/yellow]. Delete   "
            "[yellow]3[/yellow]. List   "
            "[yellow]0[/yellow]. Back"
        )
        ch = Prompt.ask("›", default="0")

        if ch == "0":
            return

        elif ch == "1":
            name = Prompt.ask("Name (e.g. Coding Assistant)")
            console.print("[dim]Enter prompt text. Finish with a line containing only '---'[/dim]")
            lines = []
            while True:
                line = input()
                if line.strip() == "---":
                    break
                lines.append(line)
            content = "\n".join(lines)
            config.system_prompts.append(SystemPrompt(name=name, content=content))
            save_config(config, fernet)
            console.print(f"[green]Prompt '{name}' saved.[/green]")

        elif ch == "2":
            idx = pick("Delete prompt", config.system_prompts, lambda p: p.name)
            if idx is not None:
                removed = config.system_prompts.pop(idx)
                save_config(config, fernet)
                console.print(f"[green]Deleted '{removed.name}'.[/green]")

        elif ch == "3":
            if not config.system_prompts:
                console.print("[yellow]No system prompts saved yet.[/yellow]")
                continue
            t = Table(title="System Prompts", show_lines=True)
            t.add_column("Name")
            t.add_column("Preview")
            for p in config.system_prompts:
                preview = p.content[:80].replace("\n", " ")
                if len(p.content) > 80:
                    preview += "…"
                t.add_row(p.name, preview)
            console.print(t)


# ── Global settings ───────────────────────────────────────────────────────────

def manage_settings(config: Config, fernet: Fernet) -> None:
    while True:
        divider("Settings")
        usage_on = config.settings.show_usage
        console.print(
            f"  [yellow]1[/yellow]. Show token/cache usage after each reply, by default: "
            f"[bold]{'on' if usage_on else 'off'}[/bold]  [dim](select to toggle)[/dim]\n"
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


def _mcp_label(s: McpServerConfig) -> str:
    return f"{s.name}  {s.command} {' '.join(s.args)}"


def manage_mcp_servers(config: Config, fernet: Fernet, mcp: McpManager) -> None:
    while True:
        divider("MCP Servers")
        console.print(
            "  [yellow]1[/yellow]. Add   "
            "[yellow]2[/yellow]. Delete   "
            "[yellow]3[/yellow]. List   "
            "[yellow]0[/yellow]. Back"
        )
        ch = Prompt.ask("›", default="0")

        if ch == "0":
            return

        elif ch == "1":
            name = Prompt.ask("Name (e.g. brave-search)")
            command = Prompt.ask("Command (e.g. python)")
            raw_args = Prompt.ask("Arguments (e.g. C:\\path\\to\\brave_search_mcp.py)", default="")
            args = [a.strip("\"'") for a in shlex.split(raw_args, posix=(os.name != "nt"))]
            env: Dict[str, str] = {}
            console.print("[dim]Environment variables for the server (e.g. BRAVE_API_KEY). "
                          "Leave the name blank to finish.[/dim]")
            while True:
                key = Prompt.ask("Env var name", default="")
                if not key:
                    break
                env[key] = getpass.getpass(f"{key} value: ")
            config.mcp_servers.append(
                McpServerConfig(name=name, command=command, args=args, env=env)
            )
            save_config(config, fernet)
            console.print(f"[green]MCP server '{name}' saved. It connects when you start a chat.[/green]")

        elif ch == "2":
            idx = pick("Delete MCP server", config.mcp_servers, _mcp_label)
            if idx is not None:
                removed = config.mcp_servers.pop(idx)
                save_config(config, fernet)
                console.print(f"[green]Deleted '{removed.name}'.[/green]")

        elif ch == "3":
            if not config.mcp_servers:
                console.print("[yellow]No MCP servers saved yet.[/yellow]")
                continue
            t = Table(title="MCP Servers", show_lines=True)
            for col in ("Name", "Command", "Env vars", "Status"):
                t.add_column(col)
            for s in config.mcp_servers:
                if s.name in mcp.errors:
                    status = f"[red]failed: {escape(mcp.errors[s.name])}[/red]"
                elif s.name in mcp.server_tools:
                    status = f"connected: {', '.join(mcp.server_tools[s.name]) or 'no tools'}"
                else:
                    status = "[dim]not connected yet[/dim]"
                t.add_row(
                    s.name,
                    escape(f"{s.command} {' '.join(s.args)}"),
                    ", ".join(s.env) or "-",
                    status,
                )
            console.print(t)
