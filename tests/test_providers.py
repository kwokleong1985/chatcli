import pytest

from chatcli.models import Session
from chatcli.providers import EFFORT_LEVELS, format_usage, reasoning_label, reasoning_params

GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"
OTHER = "https://api.openai.com/v1"


def _sess(thinking=True, effort="default", base_url=OTHER, model="gpt-4o") -> Session:
    return Session(name="n", endpoint_name="e", base_url=base_url, model=model, prompt_name="p",
                   system_prompt="s", thinking=thinking, reasoning_effort=effort)


@pytest.mark.parametrize("sess, expected", [
    # thinking on: "default" sends nothing, anything else is passed through
    (_sess(), {}),
    (_sess(effort="high"), {"reasoning_effort": "high"}),
    (_sess(effort="xhigh"), {"reasoning_effort": "xhigh"}),
    # thinking off on a normal provider
    (_sess(thinking=False), {"reasoning_effort": "none"}),
    (_sess(thinking=False, effort="high"), {"reasoning_effort": "none"}),
    # Gemini: only 2.5 non-Pro can switch reasoning off; everything else gets the lowest level
    (_sess(thinking=False, base_url=GEMINI, model="gemini-2.5-flash"), {"reasoning_effort": "none"}),
    (_sess(thinking=False, base_url=GEMINI, model="gemini-2.5-pro"), {"reasoning_effort": "minimal"}),
    (_sess(thinking=False, base_url=GEMINI, model="gemini-3-pro"), {"reasoning_effort": "minimal"}),
    # Gemini tops out at "high"
    (_sess(effort="xhigh", base_url=GEMINI, model="gemini-2.5-pro"), {"reasoning_effort": "high"}),
    (_sess(effort="low", base_url=GEMINI, model="gemini-2.5-pro"), {"reasoning_effort": "low"}),
    (_sess(base_url=GEMINI, model="gemini-2.5-pro"), {}),
])
def test_reasoning_params(sess, expected):
    assert reasoning_params(sess) == expected


def test_reasoning_label():
    assert reasoning_label(False, "high") == "off"
    assert reasoning_label(True, "high") == "on (high)"
    assert reasoning_label(True, "default") == "on (default)"


def test_effort_levels_start_with_default_and_include_none():
    assert EFFORT_LEVELS[0] == "default" and "none" in EFFORT_LEVELS and EFFORT_LEVELS[-1] == "xhigh"


# ── format_usage ──────────────────────────────────────────────────────────────

def test_usage_not_reported():
    assert format_usage(None) == "usage: not reported by provider"
    assert format_usage({}) == "usage: not reported by provider"


def test_usage_openai_shape_with_cache_and_reasoning():
    usage = {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105,
             "prompt_tokens_details": {"cached_tokens": 40},
             "completion_tokens_details": {"reasoning_tokens": 3}}
    assert format_usage(usage) == \
        "usage: prompt 100 · cached 40 (40%) · completion 5 · reasoning 3 · total 105"


def test_usage_deepseek_cache_field_is_understood():
    usage = {"prompt_tokens": 200, "prompt_cache_hit_tokens": 50, "completion_tokens": 1}
    assert format_usage(usage) == "usage: prompt 200 · cached 50 (25%) · completion 1"


def test_usage_missing_fields_show_na_rather_than_pretending_a_cache_miss():
    assert format_usage({"total_tokens": 7}) == "usage: prompt n/a · cached n/a · completion n/a · total 7"


def test_usage_zero_prompt_does_not_divide_by_zero():
    assert format_usage({"prompt_tokens": 0, "prompt_tokens_details": {"cached_tokens": 0}}) == \
        "usage: prompt 0 · cached 0 · completion n/a"
