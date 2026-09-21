"""The chat-completion call: tool-calling loop, usage totals and the API error log."""

import json
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from openai import OpenAI

from .mcp_client import McpManager
from .paths import LOG_DIR, API_ERROR_LOG
from .providers import reasoning_params

MAX_TOOL_ROUNDS = 8            # model <-> tool round trips per user message


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
