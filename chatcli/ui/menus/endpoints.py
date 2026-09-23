"""Endpoints menu: name, base URL, API key, model and reasoning settings."""

import getpass

from cryptography.fernet import Fernet
from rich.prompt import Prompt

from ...models import Config, Endpoint
from ...providers import EFFORT_LEVELS, reasoning_label
from ...store import save_config
from ..common import console, pick
from .base import CollectionMenu, MenuAction, run_collection_menu


def ep_label(e: Endpoint) -> str:
    return f"{e.name}  [{e.model}]  {e.base_url}"


def _ask_thinking(cur_thinking: bool = True, cur_effort: str = "default") -> tuple:
    """Prompt for thinking on/off and reasoning effort. Returns (thinking, effort)."""
    thinking = Prompt.ask(
        "Thinking (sends reasoning_effort=none when off)",
        choices=["on", "off"],
        default="on" if cur_thinking else "off",
    ) == "on"
    effort = "default"
    if thinking:
        effort = Prompt.ask(
            "Reasoning effort ('default' = don't send, model decides)",
            choices=[lvl for lvl in EFFORT_LEVELS if lvl != "none"],
            default=cur_effort if cur_effort in EFFORT_LEVELS and cur_effort != "none" else "default",
        )
    return thinking, effort


def _new_endpoint() -> Endpoint:
    name     = Prompt.ask("Name (e.g. OpenAI)")
    base_url = Prompt.ask("Base URL (e.g. https://api.openai.com/v1)")
    api_key  = getpass.getpass("API Key: ")
    model    = Prompt.ask("Model (e.g. gpt-4o)")
    thinking, effort = _ask_thinking()
    return Endpoint(
        name=name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        thinking=thinking,
        reasoning_effort=effort,
    )


def _change_reasoning(config: Config, fernet: Fernet) -> None:
    idx = pick("Change reasoning settings for", config.endpoints, ep_label)
    if idx is not None:
        ep = config.endpoints[idx]
        ep.thinking, ep.reasoning_effort = _ask_thinking(ep.thinking, ep.reasoning_effort)
        save_config(config, fernet)
        console.print(f"[green]Updated '{ep.name}'.[/green]")


ENDPOINTS_MENU = CollectionMenu(
    title="Endpoints",
    attr="endpoints",
    label=ep_label,
    delete_title="Delete endpoint",
    empty_message="No endpoints saved yet.",
    columns=("Name", "Base URL", "Model", "Reasoning"),
    row=lambda e: (e.name, e.base_url, e.model, reasoning_label(e.thinking, e.reasoning_effort)),
    prompt_new=_new_endpoint,
    saved_message=lambda e: f"Endpoint '{e.name}' saved.",
    extras=(MenuAction("Reasoning settings", _change_reasoning),),
)


def manage_endpoints(config: Config, fernet: Fernet) -> None:
    run_collection_menu(ENDPOINTS_MENU, config, fernet)
