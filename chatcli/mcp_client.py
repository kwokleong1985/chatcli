"""MCP (tool calling): a synchronous facade over stdio MCP servers."""

import re
import json
import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import AsyncExitStack
from typing import Optional, List, Dict, Any

from .paths import LOG_DIR

try:  # MCP is optional: chat works without it, tool calling doesn't
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

TOOL_TIMEOUT = 60              # seconds to wait for one tool call
MAX_TOOL_RESULT_CHARS = 20_000


def safe_name(name: str) -> str:
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
            open(LOG_DIR / f"mcp_{safe_name(srv['name'])}.log", "a", encoding="utf-8")
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
                exposed = f"{safe_name(srv['name'])}__{tool.name}"
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
