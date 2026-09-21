#!/usr/bin/env python3
"""ChatCLI — conversational CLI client for OpenAI-compatible APIs."""

import os
import re
import sys
import json
import shlex
import atexit
import asyncio
import base64
import getpass
import datetime
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional, List, Dict, Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from openai import OpenAI

try:  # MCP is optional: chat works without it, tool calling doesn't
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

APP_DIR = Path.home() / ".chatcli"
CONFIG_FILE = APP_DIR / "config.enc"
SALT_FILE = APP_DIR / "salt.bin"
HISTORY_DIR = APP_DIR / "history"
LOG_DIR = APP_DIR / "logs"
API_ERROR_LOG = LOG_DIR / "api_errors.jsonl"

console = Console()

# OpenAI-standard `reasoning_effort` values. "default" means the parameter is
# omitted and the model decides. "none" is OpenAI's way to disable reasoning.
EFFORT_LEVELS = ["default", "none", "minimal", "low", "medium", "high", "xhigh"]

# ── Crypto ────────────────────────────────────────────────────────────────────

def get_or_create_salt() -> bytes:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = os.urandom(16)
    SALT_FILE.write_bytes(salt)
    return salt


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def make_fernet(password: str) -> Fernet:
    return Fernet(derive_key(password, get_or_create_salt()))


# ── Config ────────────────────────────────────────────────────────────────────

EMPTY_CONFIG: Dict[str, List] = {"endpoints": [], "system_prompts": [], "mcp_servers": []}


def load_config(fernet: Fernet) -> dict:
    if not CONFIG_FILE.exists():
        return {**{k: [] for k in EMPTY_CONFIG}, "settings": {}}
    try:
        config = json.loads(fernet.decrypt(CONFIG_FILE.read_bytes()))
    except InvalidToken:
        console.print("[bold red]Wrong password or corrupted config.[/bold red]")
        sys.exit(1)
    for key in EMPTY_CONFIG:  # configs saved before a section existed lack the key
        config.setdefault(key, [])
    config.setdefault("settings", {})  # global defaults, e.g. {"show_usage": true}
    return config


def default_show_usage(config: dict) -> bool:
    return bool(config.get("settings", {}).get("show_usage", False))


def save_config(config: dict, fernet: Fernet) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_bytes(fernet.encrypt(json.dumps(config).encode()))


# ── UI helpers ────────────────────────────────────────────────────────────────

def pick(title: str, items: list, label_fn=str) -> Optional[int]:
    """Numbered selection menu. Returns index or None if user cancels."""
    if not items:
        return None
    console.print(f"\n[bold cyan]{title}[/bold cyan]")
    for i, item in enumerate(items, 1):
        console.print(f"  [yellow]{i}[/yellow]. {label_fn(item)}")
    console.print("  [dim]0. Back[/dim]")
    while True:
        raw = Prompt.ask("›", default="0")
        if raw.isdigit() and 0 <= int(raw) <= len(items):
            n = int(raw)
            return n - 1 if n > 0 else None
        console.print("[red]Enter a number from the list.[/red]")


def divider(text: str = "") -> None:
    console.rule(f"[bold cyan]{text}[/bold cyan]" if text else "")


# ── Endpoint management ───────────────────────────────────────────────────────

def _ep_label(e: dict) -> str:
    return f"{e['name']}  [{e['model']}]  {e['base_url']}"


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


def manage_endpoints(config: dict, fernet: Fernet) -> None:
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
            config["endpoints"].append({
                "name": name,
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "thinking": thinking,
                "reasoning_effort": effort,
            })
            save_config(config, fernet)
            console.print(f"[green]Endpoint '{name}' saved.[/green]")

        elif ch == "2":
            idx = pick("Delete endpoint", config["endpoints"], _ep_label)
            if idx is not None:
                removed = config["endpoints"].pop(idx)
                save_config(config, fernet)
                console.print(f"[green]Deleted '{removed['name']}'.[/green]")

        elif ch == "3":
            if not config["endpoints"]:
                console.print("[yellow]No endpoints saved yet.[/yellow]")
                continue
            t = Table(title="Endpoints", show_lines=True)
            for col in ("Name", "Base URL", "Model", "Reasoning"):
                t.add_column(col)
            for e in config["endpoints"]:
                t.add_row(
                    e["name"], e["base_url"], e["model"],
                    _reasoning_label(e.get("thinking", True), e.get("reasoning_effort", "default")),
                )
            console.print(t)

        elif ch == "4":
            idx = pick("Change reasoning settings for", config["endpoints"], _ep_label)
            if idx is not None:
                ep = config["endpoints"][idx]
                ep["thinking"], ep["reasoning_effort"] = _ask_thinking(
                    ep.get("thinking", True), ep.get("reasoning_effort", "default")
                )
                save_config(config, fernet)
                console.print(f"[green]Updated '{ep['name']}'.[/green]")


# ── System prompt management ──────────────────────────────────────────────────

def manage_prompts(config: dict, fernet: Fernet) -> None:
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
            config["system_prompts"].append({"name": name, "content": content})
            save_config(config, fernet)
            console.print(f"[green]Prompt '{name}' saved.[/green]")

        elif ch == "2":
            idx = pick("Delete prompt", config["system_prompts"], lambda p: p["name"])
            if idx is not None:
                removed = config["system_prompts"].pop(idx)
                save_config(config, fernet)
                console.print(f"[green]Deleted '{removed['name']}'.[/green]")

        elif ch == "3":
            if not config["system_prompts"]:
                console.print("[yellow]No system prompts saved yet.[/yellow]")
                continue
            t = Table(title="System Prompts", show_lines=True)
            t.add_column("Name")
            t.add_column("Preview")
            for p in config["system_prompts"]:
                preview = p["content"][:80].replace("\n", " ")
                if len(p["content"]) > 80:
                    preview += "…"
                t.add_row(p["name"], preview)
            console.print(t)


# ── Global settings ───────────────────────────────────────────────────────────

def manage_settings(config: dict, fernet: Fernet) -> None:
    while True:
        divider("Settings")
        usage_on = default_show_usage(config)
        console.print(
            f"  [yellow]1[/yellow]. Show token/cache usage after each reply, by default: "
            f"[bold]{'on' if usage_on else 'off'}[/bold]  [dim](select to toggle)[/dim]\n"
            "  [yellow]0[/yellow]. Back"
        )
        ch = Prompt.ask("›", default="0")

        if ch == "0":
            return

        elif ch == "1":
            config["settings"]["show_usage"] = not usage_on
            save_config(config, fernet)
            console.print(
                f"[green]Usage stats default: {'on' if not usage_on else 'off'}.[/green] "
                "[dim]Applies to new conversations, and to old ones that never used /usage. "
                "/usage in a chat still overrides it for that conversation.[/dim]"
            )


# ── MCP (tool calling) ────────────────────────────────────────────────────────

MAX_TOOL_ROUNDS = 8            # model <-> tool round trips per user message
TOOL_TIMEOUT = 60              # seconds to wait for one tool call
MAX_TOOL_RESULT_CHARS = 20_000


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _clean_schema(node: Any) -> Any:
    """Trim a tool's JSON Schema to what OpenAI and Gemini both accept.

    Drops the `title` metadata the MCP SDK adds and collapses Optional[X], which
    is emitted as anyOf [X, null], into plain X.
    """
    if isinstance(node, list):
        return [_clean_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    any_of = node.get("anyOf")
    if isinstance(any_of, list):
        non_null = [s for s in any_of if not (isinstance(s, dict) and s.get("type") == "null")]
        if len(non_null) == 1 and isinstance(non_null[0], dict):
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            return _clean_schema({**rest, **non_null[0]})
    # A property *named* "title" or "default" has a dict value, so only a string
    # title / null default is metadata.
    return {
        k: _clean_schema(v)
        for k, v in node.items()
        if not (k == "title" and isinstance(v, str))
        and not (k == "default" and v is None)
    }


class McpManager:
    """Runs MCP stdio servers on a background asyncio loop behind a synchronous API.

    A single long-lived task owns every connection: anyio task groups must be exited
    by the task that entered them, so connecting and disconnecting both happen in
    `_serve` rather than in separate calls.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._future = None
        self._shutdown: Optional[asyncio.Event] = None
        self._ready = threading.Event()
        self._key: Optional[str] = None
        self._tools: Dict[str, tuple] = {}     # exposed tool name -> (session, server's tool name)
        self._schemas: List[dict] = []         # OpenAI-format tool definitions
        self.server_tools: Dict[str, List[str]] = {}
        self.errors: Dict[str, str] = {}       # server name -> why it failed to connect

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools)

    def openai_tools(self) -> List[dict]:
        return list(self._schemas)

    def start(self, servers: List[dict]) -> bool:
        """Connect to `servers`. No-op (returns False) if that exact set is already running."""
        key = json.dumps(servers, sort_keys=True)
        if self._future is not None and key == self._key:
            return False
        self.close()
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._key = key
        self._tools, self._schemas, self.server_tools, self.errors = {}, [], {}, {}
        self._ready.clear()
        self._future = asyncio.run_coroutine_threadsafe(self._serve(servers), self._loop)
        self._ready.wait()
        return True

    def close(self) -> None:
        if self._future is None:
            return
        if self._shutdown is not None:
            self._loop.call_soon_threadsafe(self._shutdown.set)
        try:
            self._future.result(timeout=10)
        except Exception:
            self._future.cancel()
        self._future, self._key, self._shutdown = None, None, None
        self._tools, self._schemas, self.server_tools = {}, [], {}

    async def _serve(self, servers: List[dict]) -> None:
        self._shutdown = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                for srv in servers:
                    try:
                        await self._connect(stack, srv)
                    except Exception as exc:
                        self.errors[srv["name"]] = f"{type(exc).__name__}: {exc}"
                self._ready.set()
                await self._shutdown.wait()
        finally:
            self._ready.set()  # never leave start() blocked, even if setup blew up

    async def _connect(self, stack: AsyncExitStack, srv: dict) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        # The server's stderr goes to a file so its logging doesn't garble the terminal.
        errlog = stack.enter_context(
            open(LOG_DIR / f"mcp_{_safe_name(srv['name'])}.log", "a", encoding="utf-8")
        )
        params = StdioServerParameters(
            command=srv["command"], args=srv.get("args", []), env=srv.get("env") or None
        )
        read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
        session = await stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=30)

        names = []
        for tool in (await session.list_tools()).tools:
            exposed = tool.name
            if exposed in self._tools:  # same tool name from two servers
                exposed = f"{_safe_name(srv['name'])}__{tool.name}"
            schema = (
                getattr(tool, "input_schema", None)
                or getattr(tool, "inputSchema", None)
                or {"type": "object", "properties": {}}
            )
            self._tools[exposed] = (session, tool.name)
            self._schemas.append({
                "type": "function",
                "function": {
                    "name": exposed,
                    "description": tool.description or "",
                    "parameters": _clean_schema(schema),
                },
            })
            names.append(exposed)
        self.server_tools[srv["name"]] = names

    def call(self, name: str, args: dict) -> str:
        """Run a tool and return its output as text. Never raises: errors come back as text."""
        entry = self._tools.get(name)
        if entry is None:
            return f"Error: unknown tool '{name}'"
        session, original = entry
        future = asyncio.run_coroutine_threadsafe(session.call_tool(original, args), self._loop)
        try:
            res = future.result(timeout=TOOL_TIMEOUT)
        except FutureTimeout:
            future.cancel()
            return f"Error: tool '{name}' timed out after {TOOL_TIMEOUT}s"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

        parts = [
            c.text if getattr(c, "type", None) == "text"
            else f"[{getattr(c, 'type', 'unknown')} content omitted]"
            for c in res.content
        ]
        text = "\n".join(parts) or "(no output)"
        if getattr(res, "is_error", getattr(res, "isError", False)):
            text = f"Error: {text}"
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + "\n…[truncated]"
        return text


def prepare_mcp(config: dict, mcp: McpManager) -> None:
    """Make sure the configured MCP servers are connected before a chat starts."""
    servers = config.get("mcp_servers", [])
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
                f"[dim]  server log: {LOG_DIR / ('mcp_' + _safe_name(name) + '.log')}[/dim]"
            )


def _mcp_label(s: dict) -> str:
    return f"{s['name']}  {s['command']} {' '.join(s.get('args', []))}"


def manage_mcp_servers(config: dict, fernet: Fernet, mcp: McpManager) -> None:
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
            config["mcp_servers"].append(
                {"name": name, "command": command, "args": args, "env": env}
            )
            save_config(config, fernet)
            console.print(f"[green]MCP server '{name}' saved. It connects when you start a chat.[/green]")

        elif ch == "2":
            idx = pick("Delete MCP server", config["mcp_servers"], _mcp_label)
            if idx is not None:
                removed = config["mcp_servers"].pop(idx)
                save_config(config, fernet)
                console.print(f"[green]Deleted '{removed['name']}'.[/green]")

        elif ch == "3":
            if not config["mcp_servers"]:
                console.print("[yellow]No MCP servers saved yet.[/yellow]")
                continue
            t = Table(title="MCP Servers", show_lines=True)
            for col in ("Name", "Command", "Env vars", "Status"):
                t.add_column(col)
            for s in config["mcp_servers"]:
                if s["name"] in mcp.errors:
                    status = f"[red]failed: {escape(mcp.errors[s['name']])}[/red]"
                elif s["name"] in mcp.server_tools:
                    status = f"connected: {', '.join(mcp.server_tools[s['name']]) or 'no tools'}"
                else:
                    status = "[dim]not connected yet[/dim]"
                t.add_row(
                    s["name"],
                    escape(f"{s['command']} {' '.join(s.get('args', []))}"),
                    ", ".join(s.get("env", {})) or "-",
                    status,
                )
            console.print(t)


# ── Conversation history ──────────────────────────────────────────────────────

def _sess_label(s: dict) -> str:
    return (
        f"{s['name']}  "
        f"[{s['updated']}]  "
        f"{s['message_count']} msgs  "
        f"{s['endpoint_name']} / {s['model']}"
    )


def list_sessions() -> List[dict]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for f in sorted(HISTORY_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                **data,
                "_path": f,
                "updated": datetime.datetime.fromtimestamp(
                    f.stat().st_mtime
                ).strftime("%Y-%m-%d %H:%M"),
                "message_count": len(data.get("messages", [])),
            })
        except Exception:
            pass
    return sessions


def save_session(sess: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path: Path = sess["_path"]
    # api_key is never written to disk: it lives in the encrypted config and is
    # re-read from there on resume (see resume_session).
    serialisable = {k: v for k, v in sess.items() if not k.startswith("_") and k != "api_key"}
    path.write_text(json.dumps(serialisable, indent=2, ensure_ascii=False), encoding="utf-8")


# ── API ───────────────────────────────────────────────────────────────────────

def _reasoning_label(thinking: bool, effort: str) -> str:
    if not thinking:
        return "off"
    return f"on ({effort})"


def reasoning_params(sess: dict) -> dict:
    """Extra request fields for the session's thinking / reasoning-effort settings.

    Passed via extra_body so it works on any openai SDK version and is forwarded
    as-is to OpenAI-compatible servers.
    """
    gemini = "generativelanguage.googleapis.com" in sess.get("base_url", "")
    if not sess.get("thinking", True):
        if gemini:
            # Gemini only accepts "none" on 2.5 non-Pro models; Gemini 3 and 2.5 Pro
            # can't turn reasoning off, so use the lowest level it allows.
            model = sess.get("model", "")
            if "2.5" in model and "pro" not in model:
                return {"reasoning_effort": "none"}
            return {"reasoning_effort": "minimal"}
        return {"reasoning_effort": "none"}
    effort = sess.get("reasoning_effort", "default")
    if effort in ("default", None):
        return {}
    if gemini and effort == "xhigh":  # Gemini tops out at "high"
        effort = "high"
    return {"reasoning_effort": effort}


def log_api_error(sess: dict, request: dict, exc: Exception) -> Path:
    """Append one JSON line to the error log with the request sent and the response received.

    The API key is never logged. Note the log holds conversation text in plaintext,
    like the history files.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    response: Dict[str, Any] = {}
    http_resp = getattr(exc, "response", None)
    if http_resp is not None:
        response["status_code"] = getattr(http_resp, "status_code", None)
        try:
            response["headers"] = dict(http_resp.headers)
        except Exception:
            pass
        try:
            response["body"] = http_resp.text
        except Exception:
            response["body"] = getattr(exc, "body", None)
    elif getattr(exc, "body", None) is not None:
        response["body"] = exc.body

    entry = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "endpoint": sess.get("endpoint_name"),
        "base_url": sess.get("base_url"),
        "request": request,
        "response": response or None,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    with API_ERROR_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return API_ERROR_LOG


def _add_usage(a: Any, b: Any) -> Any:
    """Sum two usage dicts field by field (numbers add, nested dicts recurse)."""
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: _add_usage(a.get(k), b.get(k)) for k in a.keys() | b.keys()}
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + b
    return b


def ask_model(sess: dict, user_text: str, mcp: Optional["McpManager"] = None, on_tool=None) -> tuple:
    """Returns (reply_text, usage_dict_or_None). Failed calls are logged and re-raised.

    If MCP tools are available the model may call them; each call is run through `mcp`
    and its result fed back until the model answers in text. `on_tool(name, args, result)`
    is called after every tool run. Only the final text reply is returned (and later
    stored in history); intermediate tool messages exist for this one turn.
    """
    client = OpenAI(api_key=sess["api_key"], base_url=sess["base_url"])
    history: List[Dict[str, Any]] = [{"role": "system", "content": sess["system_prompt"]}]
    history += sess["messages"]
    history += [{"role": "user", "content": user_text}]
    kwargs: Dict[str, Any] = {}
    extra = reasoning_params(sess)
    if extra:
        kwargs["extra_body"] = extra
    tools = mcp.openai_tools() if mcp is not None and sess.get("tools", True) else []
    if tools:
        kwargs["tools"] = tools

    total_usage = None
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        if round_no == MAX_TOOL_ROUNDS:
            kwargs.pop("tools", None)  # out of rounds: force a plain-text answer
        try:
            resp = client.chat.completions.create(model=sess["model"], messages=history, **kwargs)
        except Exception as exc:
            try:
                log_api_error(sess, {"model": sess["model"], "messages": history, **kwargs}, exc)
            except Exception:
                pass  # never let logging mask the real API error
            raise
        if getattr(resp, "usage", None):
            total_usage = _add_usage(total_usage, resp.usage.model_dump())
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or "", total_usage

        # model_dump keeps provider extras (e.g. Gemini 3 thought signatures) that
        # must be echoed back alongside the tool results.
        history.append(msg.model_dump(exclude_none=True))
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = mcp.call(tc.function.name, args)
            except json.JSONDecodeError:
                args, result = {}, "Error: tool arguments were not valid JSON"
            if on_tool:
                on_tool(tc.function.name, args, result)
            history.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    return "(Stopped: too many tool-call rounds without a final answer.)", total_usage


def format_usage(usage: Optional[dict]) -> str:
    """One-line token/cache summary. Tolerates providers that omit fields.

    Cached-token count comes from OpenAI's usage.prompt_tokens_details.cached_tokens,
    or DeepSeek's usage.prompt_cache_hit_tokens. "n/a" means the provider didn't
    report it (which is not the same as a cache miss).
    """
    if not usage:
        return "usage: not reported by provider"
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens")

    parts = [f"prompt {prompt if prompt is not None else 'n/a'}"]
    if cached is None:
        parts.append("cached n/a")
    elif prompt:
        parts.append(f"cached {cached} ({cached / prompt:.0%})")
    else:
        parts.append(f"cached {cached}")
    parts.append(f"completion {completion if completion is not None else 'n/a'}")
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if reasoning:
        parts.append(f"reasoning {reasoning}")
    if usage.get("total_tokens") is not None:
        parts.append(f"total {usage['total_tokens']}")
    return "usage: " + " · ".join(parts)


# ── Chat loop ─────────────────────────────────────────────────────────────────

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


def _tools_label(sess: dict, mcp: Optional[McpManager]) -> str:
    if mcp is None or not mcp.tool_names:
        return "none available"
    return f"{len(mcp.tool_names)} {'on' if sess.get('tools', True) else 'off'}"


def _show_tool_call(name: str, args: dict, result: str) -> None:
    call = escape(f"{name}({json.dumps(args, ensure_ascii=False)})")
    outcome = f"[red]{escape(result[:200])}[/red]" if result.startswith("Error") else f"{len(result)} chars"
    console.print(f"[magenta]tool ›[/magenta] [dim]{call} -> [/dim]{outcome}")


def chat_loop(sess: dict, mcp: Optional[McpManager] = None) -> None:
    divider(f"Chat — {sess['name']}")
    console.print(
        f"[dim]{sess['endpoint_name']} · {sess['model']} · "
        f"prompt: {sess['prompt_name']} · "
        f"thinking: {_reasoning_label(sess.get('thinking', True), sess.get('reasoning_effort', 'default'))} · "
        f"tools: {_tools_label(sess, mcp)}[/dim]"
    )
    console.print(_HELP + "\n")

    # Replay existing history on resume
    for msg in sess["messages"]:
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
                f"[dim]Session: {sess['name']} | "
                f"Endpoint: {sess['endpoint_name']} | "
                f"Model: {sess['model']} | "
                f"Prompt: {sess['prompt_name']} | "
                f"Thinking: {_reasoning_label(sess.get('thinking', True), sess.get('reasoning_effort', 'default'))} | "
                f"Usage stats: {'on' if sess.get('show_usage', False) else 'off'} | "
                f"Tools: {_tools_label(sess, mcp)} | "
                f"Messages: {len(sess['messages'])}[/dim]"
            )
            continue
        if cmd.startswith("/tools"):
            arg = cmd[len("/tools"):].strip()
            if arg in ("on", "off"):
                sess["tools"] = arg == "on"
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
            sess["show_usage"] = arg == "on"
            save_session(sess)
            console.print(f"[dim]Usage stats: {arg}[/dim]")
            continue
        if cmd.startswith("/thinking"):
            arg = cmd[len("/thinking"):].strip()
            if arg not in ("on", "off"):
                console.print("[yellow]Usage: /thinking on|off[/yellow]")
                continue
            sess["thinking"] = arg == "on"
            save_session(sess)
            console.print(
                f"[dim]Thinking: "
                f"{_reasoning_label(sess['thinking'], sess.get('reasoning_effort', 'default'))}[/dim]"
            )
            continue
        if cmd.startswith("/effort"):
            arg = cmd[len("/effort"):].strip()
            if arg not in EFFORT_LEVELS:
                console.print(f"[yellow]Usage: /effort {'|'.join(EFFORT_LEVELS)}[/yellow]")
                continue
            if arg == "none":  # "none" is the same as thinking off
                sess["thinking"] = False
            else:
                sess["thinking"] = True
                sess["reasoning_effort"] = arg
            save_session(sess)
            console.print(
                f"[dim]Thinking: {_reasoning_label(sess['thinking'], sess['reasoning_effort'])}[/dim]"
            )
            continue
        if cmd == "/clear":
            if not sess["messages"]:
                console.print("[dim]History is already empty.[/dim]")
                continue
            confirm = Prompt.ask(
                f"Delete all {len(sess['messages'])} messages in this conversation? "
                "Endpoint, system prompt and thinking settings are kept",
                choices=["y", "n"],
                default="n",
            )
            if confirm != "y":
                continue
            # Only the messages are wiped; endpoint, prompt, thinking and effort stay.
            sess["messages"] = []
            save_session(sess)
            console.clear()
            divider(f"Chat — {sess['name']}")
            console.print(
                f"[dim]{sess['endpoint_name']} · {sess['model']} · "
                f"prompt: {sess['prompt_name']} · "
                f"thinking: {_reasoning_label(sess.get('thinking', True), sess.get('reasoning_effort', 'default'))} · "
                f"tools: {_tools_label(sess, mcp)}[/dim]"
            )
            console.print("[green]Chat history cleared.[/green]")
            continue
        if cmd == "/system":
            console.print(Panel(
                sess["system_prompt"],
                title=f"[cyan]System Prompt — {sess['prompt_name']}[/cyan]",
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

        sess["messages"].append({"role": "user", "content": user_input})
        sess["messages"].append({"role": "assistant", "content": reply})
        save_session(sess)

        console.print(Panel(
            Markdown(reply),
            title="[blue]Assistant[/blue]",
            border_style="blue",
            title_align="left",
        ))
        if sess.get("show_usage", False):
            console.print(f"[dim]{format_usage(usage)}[/dim]", highlight=False)

    console.print("[dim]Conversation saved.[/dim]")


# ── Session builders ──────────────────────────────────────────────────────────

def start_new(config: dict) -> Optional[dict]:
    idx = pick("Select endpoint", config["endpoints"], _ep_label)
    if idx is None:
        return None
    endpoint = config["endpoints"][idx]

    pidx = pick("Select system prompt", config["system_prompts"], lambda p: p["name"])
    if pidx is None:
        return None
    prompt = config["system_prompts"][pidx]

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = Prompt.ask("Conversation name", default=ts)
    path = HISTORY_DIR / f"{ts}.json"

    return {
        "name": name,
        "endpoint_name": endpoint["name"],
        "base_url": endpoint["base_url"],
        "api_key": endpoint["api_key"],
        "model": endpoint["model"],
        "prompt_name": prompt["name"],
        "system_prompt": prompt["content"],
        "thinking": endpoint.get("thinking", True),
        "reasoning_effort": endpoint.get("reasoning_effort", "default"),
        "show_usage": default_show_usage(config),
        "messages": [],
        "_path": path,
    }


def resume_session(config: dict) -> Optional[dict]:
    sessions = list_sessions()
    if not sessions:
        console.print("[yellow]No saved conversations found.[/yellow]")
        return None

    idx = pick("Resume conversation", sessions, _sess_label)
    if idx is None:
        return None

    sess = sessions[idx]

    # Always pull api_key fresh from encrypted config — never stored in history
    ep = next((e for e in config["endpoints"] if e["name"] == sess["endpoint_name"]), None)
    if ep is None:
        console.print(
            f"[yellow]Endpoint '{sess['endpoint_name']}' no longer exists. "
            "Pick a replacement:[/yellow]"
        )
        eidx = pick("Select endpoint", config["endpoints"], _ep_label)
        if eidx is None:
            return None
        ep = config["endpoints"][eidx]
        sess["endpoint_name"] = ep["name"]
        sess["model"] = ep["model"]

    sess["api_key"] = ep["api_key"]
    sess["base_url"] = ep["base_url"]
    # Older conversations never recorded the setting: follow the global default.
    sess.setdefault("show_usage", default_show_usage(config))
    return sess


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    console.print(Panel(
        "[bold]ChatCLI[/bold]\nConversational client for OpenAI-compatible APIs",
        border_style="cyan",
        expand=False,
    ))

    password = getpass.getpass("Master password: ")
    fernet = make_fernet(password)
    config = load_config(fernet)
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


if __name__ == "__main__":
    main()
