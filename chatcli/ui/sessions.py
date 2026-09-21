"""Building a session: start a new conversation or resume a saved one."""

import datetime
from typing import Optional

from rich.prompt import Prompt

from ..paths import HISTORY_DIR
from ..store import default_show_usage, list_sessions
from .common import console, pick
from .menus import ep_label


def _sess_label(s: dict) -> str:
    return (
        f"{s['name']}  "
        f"[{s['updated']}]  "
        f"{s['message_count']} msgs  "
        f"{s['endpoint_name']} / {s['model']}"
    )


def start_new(config: dict) -> Optional[dict]:
    idx = pick("Select endpoint", config["endpoints"], ep_label)
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
        eidx = pick("Select endpoint", config["endpoints"], ep_label)
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
