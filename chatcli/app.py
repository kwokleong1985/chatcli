"""Entry point: unlock the config and run the main menu."""

import sys
import atexit
import getpass

from rich.panel import Panel
from rich.prompt import Prompt

from .crypto import make_fernet
from .mcp_client import McpManager
from .store import WrongPasswordError, load_config
from .ui.chat import chat_loop
from .ui.common import console, divider
from .ui.menus import (
    manage_endpoints, manage_mcp_servers, manage_prompts, manage_settings, prepare_mcp,
)
from .ui.sessions import resume_session, start_new


def main() -> None:
    console.print(Panel(
        "[bold]ChatCLI[/bold]\nConversational client for OpenAI-compatible APIs",
        border_style="cyan",
        expand=False,
    ))

    password = getpass.getpass("Master password: ")
    fernet = make_fernet(password)
    try:
        config = load_config(fernet)
    except WrongPasswordError:
        console.print("[bold red]Wrong password or corrupted config.[/bold red]")
        sys.exit(1)
    mcp = McpManager()
    atexit.register(mcp.close)  # stop MCP server subprocesses however we exit

    while True:
        divider("Main Menu")
        console.print("  [yellow]1[/yellow]. New conversation")
        console.print("  [yellow]2[/yellow]. Resume conversation")
        console.print("  [yellow]3[/yellow]. Manage endpoints")
        console.print("  [yellow]4[/yellow]. Manage system prompts")
        console.print("  [yellow]5[/yellow]. Manage MCP servers")
        console.print("  [yellow]6[/yellow]. Settings")
        console.print("  [yellow]0[/yellow]. Exit")
        ch = Prompt.ask("›", default="0")

        if ch == "0":
            break

        elif ch == "1":
            if not config["endpoints"]:
                console.print("[yellow]No endpoints saved. Add one via option 3.[/yellow]")
                continue
            if not config["system_prompts"]:
                console.print("[yellow]No system prompts saved. Add one via option 4.[/yellow]")
                continue
            sess = start_new(config)
            if sess:
                prepare_mcp(config, mcp)
                chat_loop(sess, mcp)

        elif ch == "2":
            if not config["endpoints"]:
                console.print("[yellow]No endpoints saved. Add one via option 3.[/yellow]")
                continue
            sess = resume_session(config)
            if sess:
                prepare_mcp(config, mcp)
                chat_loop(sess, mcp)

        elif ch == "3":
            manage_endpoints(config, fernet)

        elif ch == "4":
            manage_prompts(config, fernet)

        elif ch == "5":
            manage_mcp_servers(config, fernet, mcp)

        elif ch == "6":
            manage_settings(config, fernet)

    console.print("[dim]Goodbye.[/dim]")

