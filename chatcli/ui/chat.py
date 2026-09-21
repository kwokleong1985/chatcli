"""The interactive chat loop and its slash commands."""

import json
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

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


@dataclass
class ChatContext:
    """Everything a slash command may read or change."""
    sess: Session
    mcp: Optional[McpManager]
    running: bool = True   # a command sets this to False to end the chat


# ── Slash command registry ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Command:
    names: Tuple[str, ...]                    # first is canonical; the rest are aliases
    usage: str                                # shown in the help line
    help: str
    run: Callable[[ChatContext, str], None]   # receives the lower-cased argument text


COMMANDS: List[Command] = []


def command(*names: str, help: str, usage: Optional[str] = None):
    """Register a slash command. Registration order is the order shown in the help line."""
    def register(fn: Callable[[ChatContext, str], None]):
        COMMANDS.append(Command(names, usage or names[0], help, fn))
        return fn
    return register


def _parse_on_off(arg: str, usage: str) -> Optional[bool]:
    if arg not in ("on", "off"):
        console.print(f"[yellow]Usage: {escape(usage)}[/yellow]")
        return None
    return arg == "on"


@command("/info", help="show session details")
def _cmd_info(ctx: ChatContext, arg: str) -> None:
    s = ctx.sess
    console.print(
        f"[dim]Session: {s.name} | "
        f"Endpoint: {s.endpoint_name} | "
        f"Model: {s.model} | "
        f"Prompt: {s.prompt_name} | "
        f"Thinking: {reasoning_label(s.thinking, s.reasoning_effort)} | "
        f"Usage stats: {'on' if s.show_usage else 'off'} | "
        f"Tools: {_tools_label(s, ctx.mcp)} | "
        f"Messages: {len(s.messages)}[/dim]"
    )


@command("/quit", "/exit", "/q", help="exit")
def _cmd_quit(ctx: ChatContext, arg: str) -> None:
    ctx.running = False


@command("/system", help="show system prompt")
def _cmd_system(ctx: ChatContext, arg: str) -> None:
    console.print(Panel(
        escape(ctx.sess.system_prompt),
        title=f"[cyan]System Prompt — {ctx.sess.prompt_name}[/cyan]",
        border_style="cyan",
    ))


@command("/thinking", usage="/thinking on|off", help="toggle thinking")
def _cmd_thinking(ctx: ChatContext, arg: str) -> None:
    value = _parse_on_off(arg, "/thinking on|off")
    if value is None:
        return
    ctx.sess.thinking = value
    save_session(ctx.sess)
    console.print(
        f"[dim]Thinking: {reasoning_label(ctx.sess.thinking, ctx.sess.reasoning_effort)}[/dim]"
    )


@command("/effort", usage="/effort <level>", help="set reasoning effort")
def _cmd_effort(ctx: ChatContext, arg: str) -> None:
    if arg not in EFFORT_LEVELS:
        console.print(f"[yellow]Usage: /effort {'|'.join(EFFORT_LEVELS)}[/yellow]")
        return
    if arg == "none":  # "none" is the same as thinking off
        ctx.sess.thinking = False
    else:
        ctx.sess.thinking = True
        ctx.sess.reasoning_effort = arg
    save_session(ctx.sess)
    console.print(
        f"[dim]Thinking: {reasoning_label(ctx.sess.thinking, ctx.sess.reasoning_effort)}[/dim]"
    )


@command("/usage", usage="/usage on|off", help="show token/cache stats per reply")
def _cmd_usage(ctx: ChatContext, arg: str) -> None:
    value = _parse_on_off(arg, "/usage on|off")
    if value is None:
        return
    ctx.sess.show_usage = value
    save_session(ctx.sess)
    console.print(f"[dim]Usage stats: {arg}[/dim]")


@command("/tools", usage="/tools [on|off]", help="list / toggle MCP tools")
def _cmd_tools(ctx: ChatContext, arg: str) -> None:
    mcp = ctx.mcp
    if arg in ("on", "off"):
        ctx.sess.tools = arg == "on"
        save_session(ctx.sess)
        console.print(f"[dim]Tools: {_tools_label(ctx.sess, mcp)}[/dim]")
    elif arg == "":
        if mcp is None or not mcp.server_tools:
            console.print("[yellow]No MCP tools available. Add a server under "
                          "'Manage MCP servers' in the main menu.[/yellow]")
        for server, names in (mcp.server_tools.items() if mcp else []):
            console.print(f"[dim]{escape(server)}:[/dim] {', '.join(names) or 'no tools'}")
    else:
        console.print(f"[yellow]Usage: {escape('/tools [on|off]')}[/yellow]")


@command("/clear", help="delete chat history (keeps settings)")
def _cmd_clear(ctx: ChatContext, arg: str) -> None:
    sess = ctx.sess
    if not sess.messages:
        console.print("[dim]History is already empty.[/dim]")
        return
    confirm = Prompt.ask(
        f"Delete all {len(sess.messages)} messages in this conversation? "
        "Endpoint, system prompt and thinking settings are kept",
        choices=["y", "n"],
        default="n",
    )
    if confirm != "y":
        return
    # Only the messages are wiped; endpoint, prompt, thinking and effort stay.
    sess.messages = []
    save_session(sess)
    console.clear()
    _print_banner(ctx)
    console.print("[green]Chat history cleared.[/green]")


_BY_NAME: Dict[str, Command] = {name: c for c in COMMANDS for name in c.names}
_HELP = "    ".join(f"[dim]{escape(c.usage)}[/dim]  {c.help}" for c in COMMANDS)


# ── Rendering ─────────────────────────────────────────────────────────────────

def _tools_label(sess: Session, mcp: Optional[McpManager]) -> str:
    if mcp is None or not mcp.tool_names:
        return "none available"
    return f"{len(mcp.tool_names)} {'on' if sess.tools else 'off'}"


def _print_banner(ctx: ChatContext) -> None:
    s = ctx.sess
    divider(f"Chat — {s.name}")
    console.print(
        f"[dim]{s.endpoint_name} · {s.model} · "
        f"prompt: {s.prompt_name} · "
        f"thinking: {reasoning_label(s.thinking, s.reasoning_effort)} · "
        f"tools: {_tools_label(s, ctx.mcp)}[/dim]"
    )


def _print_user(text: str) -> None:
    console.print(Panel(escape(text), title="[green]You[/green]", border_style="green", title_align="left"))


def _print_assistant(text: str) -> None:
    console.print(Panel(
        Markdown(text), title="[blue]Assistant[/blue]", border_style="blue", title_align="left",
    ))


def _show_tool_call(name: str, args: dict, result: str) -> None:
    call = escape(f"{name}({json.dumps(args, ensure_ascii=False)})")
    outcome = f"[red]{escape(result[:200])}[/red]" if result.startswith("Error") else f"{len(result)} chars"
    console.print(f"[magenta]tool ›[/magenta] [dim]{call} -> [/dim]{outcome}")


# ── Chat loop ─────────────────────────────────────────────────────────────────

def _send(ctx: ChatContext, text: str) -> None:
    """Send one user message to the model and show the reply. History is saved only on success."""
    sess = ctx.sess
    _print_user(text)

    with console.status("[blue]Thinking…[/blue]", spinner="dots"):
        try:
            reply, usage = ask_model(sess, text, ctx.mcp, _show_tool_call)
        except Exception as exc:
            console.print(f"[red]API error: {escape(str(exc))}[/red]")
            console.print(f"[dim]Request/response logged to {API_ERROR_LOG}[/dim]")
            return

    sess.messages.append({"role": "user", "content": text})
    sess.messages.append({"role": "assistant", "content": reply})
    save_session(sess)

    _print_assistant(reply)
    if sess.show_usage:
        console.print(f"[dim]{format_usage(usage)}[/dim]", highlight=False)


def chat_loop(sess: Session, mcp: Optional[McpManager] = None) -> None:
    ctx = ChatContext(sess, mcp)
    _print_banner(ctx)
    console.print(_HELP + "\n")

    # Replay existing history on resume
    for msg in sess.messages:
        if msg["role"] == "user":
            _print_user(msg["content"])
        elif msg["role"] == "assistant":
            _print_assistant(msg["content"])

    while ctx.running:
        try:
            user_input = Prompt.ask("\n[bold green]You[/bold green]").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input:
            continue

        name, *rest = user_input.lower().split(None, 1)
        cmd = _BY_NAME.get(name)
        if cmd:
            cmd.run(ctx, rest[0].strip() if rest else "")
        else:
            _send(ctx, user_input)

    console.print("[dim]Conversation saved.[/dim]")
