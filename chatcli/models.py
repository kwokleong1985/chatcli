"""Typed models for the saved config and for conversations.

`from_dict` ignores keys it doesn't know and fills missing ones with defaults, so
files written by older versions keep loading. `to_dict` is the on-disk shape.
"""

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_VERSION = 1  # bump when the config layout changes in a way defaults can't absorb


class _Record:
    """Shared dict conversion for flat dataclasses."""

    @classmethod
    def from_dict(cls, data: dict):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class Endpoint(_Record):
    name: str
    base_url: str
    api_key: str
    model: str
    thinking: bool = True
    reasoning_effort: str = "default"


@dataclass
class SystemPrompt(_Record):
    name: str
    content: str


@dataclass
class McpServerConfig(_Record):
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class Settings(_Record):
    show_usage: bool = False  # global default for per-reply token/cache stats


@dataclass
class Config:
    endpoints: List[Endpoint] = field(default_factory=list)
    system_prompts: List[SystemPrompt] = field(default_factory=list)
    mcp_servers: List[McpServerConfig] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        return cls(
            endpoints=[Endpoint.from_dict(e) for e in data.get("endpoints", [])],
            system_prompts=[SystemPrompt.from_dict(p) for p in data.get("system_prompts", [])],
            mcp_servers=[McpServerConfig.from_dict(s) for s in data.get("mcp_servers", [])],
            settings=Settings.from_dict(data.get("settings", {})),
        )

    def to_dict(self) -> dict:
        return {
            "version": CONFIG_VERSION,
            "endpoints": [e.to_dict() for e in self.endpoints],
            "system_prompts": [p.to_dict() for p in self.system_prompts],
            "mcp_servers": [s.to_dict() for s in self.mcp_servers],
            "settings": self.settings.to_dict(),
        }


# Fields that exist only in memory. `api_key` lives in the encrypted config and is
# re-read from there on resume, so it never reaches the history files.
_SESSION_TRANSIENT = {"api_key", "path", "updated"}


@dataclass
class Session:
    name: str
    endpoint_name: str
    base_url: str
    model: str
    prompt_name: str
    system_prompt: str
    api_key: str = ""
    thinking: bool = True
    reasoning_effort: str = "default"
    show_usage: Optional[bool] = None  # None: follow the global default (set on resume)
    tools: bool = True
    messages: List[Dict[str, Any]] = field(default_factory=list)
    path: Optional[Path] = None        # history file; set when the session is created or listed
    updated: str = ""                  # last-modified label, filled in by list_sessions

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        names = {f.name for f in fields(cls)} - _SESSION_TRANSIENT
        return cls(**{k: v for k, v in data.items() if k in names})

    def to_dict(self) -> dict:
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name not in _SESSION_TRANSIENT
        }
