"""Provider-specific behaviour: reasoning parameters and usage reporting."""

from typing import Optional

# OpenAI-standard `reasoning_effort` values. "default" means the parameter is
# omitted and the model decides. "none" is OpenAI's way to disable reasoning.
EFFORT_LEVELS = ["default", "none", "minimal", "low", "medium", "high", "xhigh"]


def reasoning_label(thinking: bool, effort: str) -> str:
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
