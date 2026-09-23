"""The chat-completion call: streaming, the tool-calling loop, usage totals and the
API error log."""

import json
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

from openai import OpenAI

from .mcp_client import McpManager
from .models import Session
from .paths import LOG_DIR, API_ERROR_LOG
from .providers import reasoning_params

MAX_TOOL_ROUNDS = 8            # model <-> tool round trips per user message

OnDelta = Optional[Callable[[str], None]]


def log_api_error(sess: Session, request: dict, exc: Exception) -> Path:
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
        "endpoint": sess.endpoint_name,
        "base_url": sess.base_url,
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


def _merge_tool_call_delta(entry: Dict[str, Any], delta: Dict[str, Any]) -> None:
    """Fold one streamed tool-call delta into the call being assembled.

    Unlike `content`, only `function.arguments` genuinely streams in fragments here;
    `id`, `type` and `function.name` are sent whole (some local servers, e.g. LM
    Studio, even resend the same `type` on more than one chunk), so those overwrite
    instead of concatenating to avoid doubled-up values like `"functionfunction"`.
    """
    for key, value in delta.items():
        if value is None:
            continue
        if key == "function":
            fn = entry.setdefault("function", {})
            for k, v in value.items():
                if v is None:
                    continue
                fn[k] = fn.get(k, "") + v if k == "arguments" and isinstance(v, str) else v
        else:
            entry[key] = value


def _merge_delta(acc: Dict[str, Any], delta: Dict[str, Any], on_delta: OnDelta) -> None:
    """Fold one streamed delta into the message being assembled for this round.

    String fields (content, and provider extras like reasoning_content or a Gemini
    thought signature) are concatenated fragment by fragment; tool_calls arrive
    piecemeal per `index` and are merged by that index (see `_merge_tool_call_delta`),
    the same shape OpenAI's own streaming examples accumulate. `on_delta` fires for
    every text fragment except `role` (sent once, whole, and not something a caller
    wants to "see" streaming) so callers can show live progress even for fields, like
    reasoning content, that never reach the saved history.
    """
    for key, value in delta.items():
        if value is None:
            continue
        if key == "tool_calls":
            calls = acc.setdefault("tool_calls", {})
            for tc in value:
                entry = calls.setdefault(tc["index"], {})
                _merge_tool_call_delta(entry, {k: v for k, v in tc.items() if k != "index"})
            continue
        if isinstance(value, str):
            if on_delta and key != "role":
                on_delta(value)
            acc[key] = acc.get(key, "") + value
        elif isinstance(value, dict):
            _merge_delta(acc.setdefault(key, {}), value, on_delta)
        else:
            acc[key] = value


def ask_model(sess: Session, user_text: str, mcp: Optional[McpManager] = None,
              on_tool=None, on_delta: OnDelta = None) -> tuple:
    """Returns (reply_text, usage_dict_or_None). Failed calls are logged and re-raised.

    The reply streams in; `on_delta(fragment)` fires for every piece of text the model
    sends (including reasoning content that never ends up in the returned reply), so
    callers can show live progress instead of waiting in silence. If MCP tools are
    available the model may call them; each call is run through `mcp` and its result
    fed back until the model answers in text. `on_tool(name, args, result)` is called
    after every tool run. Only the final text reply is returned (and later stored in
    history); intermediate tool messages exist for this one turn.
    """
    client = OpenAI(api_key=sess.api_key, base_url=sess.base_url)
    history: List[Dict[str, Any]] = [{"role": "system", "content": sess.system_prompt}]
    history += sess.messages
    history += [{"role": "user", "content": user_text}]
    kwargs: Dict[str, Any] = {"stream": True, "stream_options": {"include_usage": True}}
    extra = reasoning_params(sess)
    if extra:
        kwargs["extra_body"] = extra
    tools = mcp.openai_tools() if mcp is not None and sess.tools else []
    if tools:
        kwargs["tools"] = tools

    total_usage = None
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        if round_no == MAX_TOOL_ROUNDS:
            kwargs.pop("tools", None)  # out of rounds: force a plain-text answer
        acc: Dict[str, Any] = {}
        usage = None
        try:
            for chunk in client.chat.completions.create(model=sess.model, messages=history, **kwargs):
                if getattr(chunk, "usage", None):
                    usage = chunk.usage.model_dump()
                if not chunk.choices:  # the trailing usage-only chunk has none
                    continue
                delta = chunk.choices[0].delta.model_dump(exclude_none=True)
                _merge_delta(acc, delta, on_delta)
        except Exception as exc:
            try:
                log_api_error(sess, {"model": sess.model, "messages": history, **kwargs}, exc)
            except Exception:
                pass  # never let logging mask the real API error
            raise
        if usage:
            total_usage = _add_usage(total_usage, usage)

        tool_calls = acc.pop("tool_calls", None)
        if not tool_calls:
            return acc.get("content", "") or "", total_usage

        history.append({
            "role": "assistant",
            "content": acc.get("content", ""),
            **{k: v for k, v in acc.items() if k != "content"},
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
        })
        for tc in history[-1]["tool_calls"]:
            try:
                args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                result = mcp.call(tc["function"]["name"], args)
            except json.JSONDecodeError:
                args, result = {}, "Error: tool arguments were not valid JSON"
            if on_tool:
                on_tool(tc["function"]["name"], args, result)
            history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    return "(Stopped: too many tool-call rounds without a final answer.)", total_usage
