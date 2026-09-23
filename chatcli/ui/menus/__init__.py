"""Management menus: endpoints, system prompts, MCP servers and settings.

Split into one module per managed collection (plus `base`, the generic
Add/Delete/List engine) so a change to one collection's menu only touches its
own small file. This package re-exports the entry points `app.py` calls.
"""

from .endpoints import manage_endpoints
from .mcp import manage_mcp_servers, prepare_mcp
from .prompts import manage_prompts
from .settings import manage_settings

__all__ = [
    "manage_endpoints",
    "manage_mcp_servers",
    "manage_prompts",
    "manage_settings",
    "prepare_mcp",
]
