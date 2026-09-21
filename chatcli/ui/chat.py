"""The interactive chat loop and its slash commands."""

import json
from typing import Optional

from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt

from ..llm import ask_model
from ..mcp_client import McpManager
from ..models import Session
from ..paths import API_ERROR_LOG
from ..providers import EFFORT_LEVELS, format_usage, reasoning_label
from ..store import save_session
from .common import console, divider


_HELP = (
    "[dim]/info[/dim]  show session details    "
    "[dim]/quit[/dim]  exit    "
    "[dim]/system[/dim]  show system prompt    "
    "[dim]/thinking on|off[/dim]  toggle thinking    "
    "[dim]/effort <level>[/dim]  set reasoning effort    "
    "[dim]/usage on|off[/dim]  show token/cache stats per reply    "
    "[dim]/tools [on|off][/dim]  list / toggle MCP tools    "
    "[dim]/clear[/dim]  delete chat history (keeps settings)"
)


def _tools_label(sess: Session, mcp: Optional[McpManager]) -> str:
    if mcp is None or not mcp.tool_names:
        return "none available"
    return f"{len(mcp.tool_names)} {'on' if sess.tools else 'off'}"


def _show_tool_call(name: str, args: dict, result: str) -> None:
    call = escape(f"{name}({json.dumps(args, ensure_ascii=False)})")
    outcome = f"[red]{escape(result[:200])}[/red]" if result.startswith("Error") else f"{len(result)} chars"
    console.print(f"[magenta]tool ›[/magenta] [dim]{call} -> [/dim]{outcome}")


def chat_loop(sess: Session, mcp: Optional[McpManager] = None) -> None:
    divider(f"Chat — {sess.name}")
    console.print(
        f"[dim]{sess.endpoint_name} · {sess.model} · "
        f"prompt: {sess.prompt_name} · "
        f"thinking: {reasoning_label(sess.thinking, sess.reasoning_effort)} · "
        f"tools: {_tools_label(sess, mcp)}[/dim]"
    )
    console.print(_HELP + "\n")

    # Replay existing history on resume
    for msg in sess.messages:
        if msg["role"] == "user":
            console.print(Panel(
                msg["content"],
                title="[green]You[/green]",
                border_style="green",
                title_align="left",
            ))
        elif msg["role"] == "assistant":
            console.print(Panel(
                Markdown(msg["content"]),
                title="[blue]Assistant[/blue]",
                border_style="blue",
                title_align="left",
            ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold green]You[/bold green]").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd in ("/quit", "/exit", "/q"):
            break
        if cmd == "/info":
            console.print(
                f"[dim]Session: {sess.name} | "
                f"Endpoint: {sess.endpoint_name} | "
                f"Model: {sess.model} | "
                f"Prompt: {sess.prompt_name} | "
                f"Thinking: {reasoning_label(sess.thinking, sess.reasoning_effort)} | "
                f"Usage stats: {'on' if sess.show_usage else 'off'} | "
                f"Tools: {_tools_label(sess, mcp)} | "
                f"Messages: {len(sess.messages)}[/dim]"
            )
            continue
        if cmd.startswith("/tools"):
            arg = cmd[len("/tools"):].strip()
            if arg in ("on", "off"):
                sess.tools = arg == "on"
                save_session(sess)
                console.print(f"[dim]Tools: {_tools_label(sess, mcp)}[/dim]")
            elif arg == "":
                if mcp is None or not mcp.server_tools:
                    console.print("[yellow]No MCP tools available. Add a server under "
                                  "'Manage MCP servers' in the main menu.[/yellow]")
                for server, names in (mcp.server_tools.items() if mcp else []):
                    console.print(f"[dim]{escape(server)}:[/dim] {', '.join(names) or 'no tools'}")
            else:
                console.print("[yellow]Usage: /tools [on|off][/yellow]")
            continue
        if cmd.startswith("/usage"):
            arg = cmd[len("/usage"):].strip()
            if arg not in ("on", "off"):
                console.print("[yellow]Usage: /usage on|off[/yellow]")
                continue
            sess.show_usage = arg == "on"
            save_session(sess)
            console.print(f"[dim]Usage stats: {arg}[/dim]")
            continue
        if cmd.startswith("/thinking"):
            arg = cmd[len("/thinking"):].strip()
            if arg not in ("on", "off"):
                console.print("[yellow]Usage: /thinking on|off[/yellow]")
                continue
            sess.thinking = arg == "on"
            save_session(sess)
            console.print(
                f"[dim]Thinking: "
                f"{reasoning_label(sess.thinking, sess.reasoning_effort)}[/dim]"
            )
            continue
        if cmd.startswith("/effort"):
            arg = cmd[len("/effort"):].strip()
            if arg not in EFFORT_LEVELS:
                console.print(f"[yellow]Usage: /effort {'|'.join(EFFORT_LEVELS)}[/yellow]")
                continue
            if arg == "none":  # "none" is the same as thinking off
                sess.thinking = False
            else:
                sess.thinking = True
                sess.reasoning_effort = arg
            save_session(sess)
            console.print(
                f"[dim]Thinking: {reasoning_label(sess.thinking, sess.reasoning_effort)}[/dim]"
            )
            continue
        if cmd == "/clear":
            if not sess.messages:
                console.print("[dim]History is already empty.[/dim]")
                continue
            confirm = Prompt.ask(
                f"Delete all {len(sess.messages)} messages in this conversation? "
                "Endpoint, system prompt and thinking settings are kept",
                choices=["y", "n"],
                default="n",
            )
            if confirm != "y":
                continue
            # Only the messages are wiped; endpoint, prompt, thinking and effort stay.
            sess.messages = []
            save_session(sess)
            console.clear()
            divider(f"Chat — {sess.name}")
            console.print(
                f"[dim]{sess.endpoint_name} · {sess.model} · "
                f"prompt: {sess.prompt_name} · "
                f"thinking: {reasoning_label(sess.thinking, sess.reasoning_effort)} · "
                f"tools: {_tools_label(sess, mcp)}[/dim]"
            )
            console.print("[green]Chat history cleared.[/green]")
            continue
        if cmd == "/system":
            console.print(Panel(
                sess.system_prompt,
                title=f"[cyan]System Prompt — {sess.prompt_name}[/cyan]",
                border_style="cyan",
            ))
            continue

        console.print(Panel(
            user_input,
            title="[green]You[/green]",
            border_style="green",
            title_align="left",
        ))

        with console.status("[blue]Thinking…[/blue]", spinner="dots"):
            try:
                reply, usage = ask_model(sess, user_input, mcp, _show_tool_call)
            except Exception as exc:
                console.print(f"[red]API error: {exc}[/red]")
                console.print(f"[dim]Request/response logged to {API_ERROR_LOG}[/dim]")
                continue

        sess.messages.append({"role": "user", "content": user_input})
        sess.messages.append({"role": "assistant", "content": reply})
        save_session(sess)

        console.print(Panel(
            Markdown(reply),
            title="[blue]Assistant[/blue]",
            border_style="blue",
            title_align="left",
        ))
        if sess.show_usage:
            console.print(f"[dim]{format_usage(usage)}[/dim]", highlight=False)

    console.print("[dim]Conversation saved.[/dim]")

