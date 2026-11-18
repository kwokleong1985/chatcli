#!/usr/bin/env python3
"""Multi-URL Fetch MCP server (stdio). Fetches multiple URLs concurrently and returns their content."""

import asyncio
from typing import Optional

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("multi-fetch")

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_BYTES = 500_000  # 500 KB per URL — avoids flooding context with huge pages


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int,
    headers: dict,
) -> dict:
    """Fetch a single URL and return a result dict."""
    try:
        resp = await client.get(url, headers=headers, follow_redirects=True)
        content = resp.text[:max_bytes]
        return {
            "url": url,
            "status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "content": content,
            "truncated": len(resp.text) > max_bytes,
            "error": None,
        }
    except httpx.TimeoutException:
        return {"url": url, "status": None, "content": None, "error": "Request timed out"}
    except httpx.HTTPError as exc:
        return {"url": url, "status": None, "content": None, "error": str(exc)}


def _format_results(results: list[dict]) -> str:
    blocks = []
    for r in results:
        if r.get("error"):
            blocks.append(f"### {r['url']}\nERROR: {r['error']}")
        else:
            truncation_note = " [TRUNCATED]" if r.get("truncated") else ""
            blocks.append(
                f"### {r['url']}\n"
                f"Status: {r['status']} | Content-Type: {r.get('content_type', '')}{truncation_note}\n\n"
                f"{r['content']}"
            )
    return "\n\n---\n\n".join(blocks)


@mcp.tool()
async def fetch_multiple(
    urls: list[str],
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes_per_url: int = DEFAULT_MAX_BYTES,
    user_agent: Optional[str] = None,
) -> str:
    """Fetch multiple URLs concurrently and return their full content.

    Args:
        urls: List of URLs to fetch (http or https).
        timeout: Per-request timeout in seconds (default 20).
        max_bytes_per_url: Maximum response body size per URL in bytes (default 500000).
                           Responses are truncated at this limit to avoid flooding context.
        user_agent: Optional User-Agent header override.
    """
    if not urls:
        raise ToolError("No URLs provided.")
    if len(urls) > 20:
        raise ToolError("Maximum 20 URLs per call.")

    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    else:
        headers["User-Agent"] = (
            "Mozilla/5.0 (compatible; MultifetchMCP/1.0; +https://github.com/antigravity)"
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [_fetch_one(client, url, max_bytes_per_url, headers) for url in urls]
        results = await asyncio.gather(*tasks)

    return _format_results(results)


@mcp.tool()
async def fetch_one(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: Optional[str] = None,
) -> str:
    """Fetch a single URL and return its content.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds (default 20).
        max_bytes: Maximum response body size in bytes (default 500000).
        user_agent: Optional User-Agent header override.
    """
    headers = {"User-Agent": user_agent or "Mozilla/5.0 (compatible; MultietchMCP/1.0)"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        result = await _fetch_one(client, url, max_bytes, headers)
    return _format_results([result])


if __name__ == "__main__":
    mcp.run(transport="stdio")
