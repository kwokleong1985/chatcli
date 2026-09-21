#!/usr/bin/env python3
"""Brave Search MCP server (stdio). Requires BRAVE_API_KEY in the environment."""

import asyncio
import os
import time
from typing import Optional

import httpx
from mcp.server.mcpserver import MCPServer  # mcp>=2 (was FastMCP in mcp 1.x)
from mcp.server.mcpserver.exceptions import ToolError  # message is shown to the client

BRAVE_BASE = "https://api.search.brave.com/res/v1"

# Brave's free plan allows ~1 request/second. Requests are spaced at least this far
# apart (override with BRAVE_MIN_INTERVAL for paid plans), and a 429 is retried.
MIN_INTERVAL = float(os.environ.get("BRAVE_MIN_INTERVAL", "1.1"))
MAX_429_RETRIES = 2

mcp = MCPServer("brave-search")

_throttle_lock = asyncio.Lock()
_last_request = 0.0


async def _throttle() -> None:
    """Block until MIN_INTERVAL has passed since the previous request started."""
    global _last_request
    async with _throttle_lock:  # holding the lock while sleeping queues concurrent callers
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()


def _retry_delay(resp: httpx.Response) -> float:
    for header in ("Retry-After", "X-RateLimit-Reset"):
        try:
            return min(float(resp.headers[header]), 10.0)
        except (KeyError, ValueError):
            continue
    return MIN_INTERVAL + 0.5


async def _brave_get(endpoint: str, params: dict) -> dict:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        raise ToolError("BRAVE_API_KEY environment variable is not set")
    params = {k: v for k, v in params.items() if v is not None}
    for attempt in range(MAX_429_RETRIES + 1):
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{BRAVE_BASE}/{endpoint}",
                    params=params,
                    headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                )
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not reach Brave API: {exc}") from exc
        if resp.status_code == 429 and attempt < MAX_429_RETRIES:
            await asyncio.sleep(_retry_delay(resp))
            continue
        break
    if resp.status_code != 200:
        raise ToolError(f"Brave API error {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _format(results: list) -> str:
    if not results:
        return "No results found."
    blocks = []
    for i, r in enumerate(results, 1):
        lines = [f"{i}. {r.get('title', '(no title)')}", f"   {r.get('url', '')}"]
        if r.get("age"):
            lines.append(f"   Published: {r['age']}")
        if r.get("description"):
            lines.append(f"   {r['description']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@mcp.tool()
async def brave_web_search(
    query: str,
    count: int = 10,
    offset: int = 0,
    country: Optional[str] = None,
    freshness: Optional[str] = None,
) -> str:
    """Search the web with Brave Search.

    Args:
        query: Search query.
        count: Number of results, 1-20.
        offset: Page offset for pagination (0-9).
        country: Optional 2-letter country code, e.g. "US".
        freshness: Optional recency filter: "pd" (past day), "pw" (week), "pm" (month), "py" (year).
    """
    data = await _brave_get("web/search", {
        "q": query,
        "count": max(1, min(count, 20)),
        "offset": max(0, min(offset, 9)),
        "country": country,
        "freshness": freshness,
    })
    return _format(data.get("web", {}).get("results", []))


@mcp.tool()
async def brave_news_search(
    query: str,
    count: int = 10,
    freshness: Optional[str] = None,
) -> str:
    """Search recent news articles with Brave Search.

    Args:
        query: Search query.
        count: Number of results, 1-20.
        freshness: Optional recency filter: "pd" (past day), "pw" (week), "pm" (month), "py" (year).
    """
    data = await _brave_get("news/search", {
        "q": query,
        "count": max(1, min(count, 20)),
        "freshness": freshness,
    })
    return _format(data.get("results", []))


if __name__ == "__main__":
    mcp.run(transport="stdio")
