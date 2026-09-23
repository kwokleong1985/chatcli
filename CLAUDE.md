# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ChatCLI is a Windows-oriented, terminal-based conversational client for any OpenAI-compatible chat API (OpenAI, DeepSeek, local servers, Gemini via its OpenAI-compat endpoint, etc.), with an encrypted local config, saved conversation history, and optional MCP tool calling.

## Commands

```bash
pip install -r requirements-dev.txt   # runtime deps + pytest
python -m pytest                      # run the whole suite (~171 tests, a few seconds)
python -m pytest -v                   # list every test as it runs
python -m pytest -x                   # stop at first failure
python -m pytest tests/test_llm.py    # run one file
python -m pytest tests/test_chat.py::test_quit_stops_the_loop   # run one test
python -m pytest -k "markup or escape"   # run tests matching a keyword
python -m pytest --durations=5        # slowest tests

python -m chatcli                     # run the app from the repo root
chatcli.bat                           # Windows launcher
install.bat                           # `pip install -r requirements.txt` on Windows
```

There is no linter/formatter configured in this repo — don't invent one.

### Test isolation (important when writing or debugging tests)

`tests/conftest.py` redirects `HOME`/`USERPROFILE` to a throwaway temp directory *before* any `chatcli` module is imported (paths are resolved at import time in `chatcli/paths.py`), then asserts the resolved `APP_DIR` is actually inside that temp dir — if not, the whole run aborts rather than risk touching the real `~/.chatcli`. The `clean_app_dir` autouse fixture wipes that directory before every test. Tests never hit the network: the model client is faked, and MCP tests run the bundled Brave server with a dummy key without ever calling out. Key shared fixtures in `conftest.py`: `out` (captures rich console output), `prompts("1", "0")` (scripts `Prompt.ask` answers, raises if the app asks for more than scripted), and `session`/`endpoint`/`system_prompt`/`fernet` (ready-made test objects).

## Architecture

The package lives in `chatcli/`. Data flows: `app.py` (main menu loop) → `ui/menus.py` (manage endpoints/prompts/MCP servers/settings) or `ui/sessions.py` (pick/create a `Session`) → `ui/chat.py` (`chat_loop`, the interactive REPL) → `llm.py` (`ask_model`, one turn including any tool-call round-trips) → `providers.py` (per-provider request tweaks) and `mcp_client.py` (tool execution).

- **Persistence split.** Two independent stores, both under `~/.chatcli` (`paths.py`), never under the repo:
  - `config.enc` — endpoints, system prompts, MCP server definitions, global settings. Whole-file Fernet-encrypted JSON, keyed by a master password via PBKDF2 (`crypto.py`). Loaded/saved as a unit through `store.py::load_config`/`save_config`.
  - `history/*.json` — one plaintext JSON file per saved conversation (`Session`). `Session.to_dict()` deliberately excludes `api_key` (and `path`/`updated`) — the key lives only in the encrypted config and is re-read from there when a session is resumed, so it never lands in a history file on disk.
  - All writes go through `fileio.py::atomic_write_bytes/text` (write to a same-directory temp file, fsync, `os.replace`) so a crash never leaves a half-written config or history file.
- **Typed models with lenient loading** (`models.py`). Dataclasses model the config and `Session`. `from_dict` ignores unknown keys and fills missing ones from dataclass defaults, so files written by older ChatCLI versions keep loading without migration code — only bump `CONFIG_VERSION` and add real migration logic when a change can't be defaulted away (see `store.py::load_config`'s version check, which refuses to load a config from a *newer* version to avoid silently dropping fields on save).
- **Provider abstraction is minimal and centralized** in `providers.py`: reasoning/thinking params are built once in `reasoning_params()` (including Gemini-specific quirks — it can't fully disable reasoning on some models, tops out at `"high"` effort, etc.) and sent via `extra_body` so they work across `openai` SDK versions and are forwarded as-is by OpenAI-compatible servers. `format_usage()` similarly normalizes usage reporting across providers (e.g. DeepSeek's `prompt_cache_hit_tokens` vs OpenAI's `prompt_tokens_details.cached_tokens`).
- **MCP tool calling is optional and isolated** (`mcp_client.py`). The `mcp` package is imported defensively (`MCP_AVAILABLE` flag); the app works without it, just without tools. `McpManager` runs all configured stdio MCP servers on a single background asyncio event loop (in its own thread) behind a synchronous API, because anyio task groups must be entered and exited by the same task — so connect and disconnect both happen inside one long-lived `_serve` coroutine, not in separate calls. `_clean_schema()` trims MCP tool JSON Schemas (drops `title` metadata, collapses `Optional[X]`/`anyOf` nullables) to what OpenAI- and Gemini-compatible endpoints accept. Each server's stderr is redirected to its own log file under `~/.chatcli/logs/` so a noisy server can't garble the terminal.
- **Slash commands are a registration-based table**, not a big if/elif chain: `ui/chat.py` has a `@command("/name", ..., help=...)` decorator that appends to a module-level `COMMANDS` list, so the in-chat help text and dispatch table are generated from the same registry. Add a new command by writing a decorated function; don't add a manual branch elsewhere.
- **Management menus (`ui/menus.py`) are also table-driven**: a single `CollectionMenu` dataclass (title, attribute name, per-row renderer, "new item" prompt function, etc.) plus one generic `run_collection_menu()` loop implements Add/Delete/List for endpoints, system prompts, and MCP servers. Add a new managed collection by describing a `CollectionMenu`, not by writing a new menu loop.
- **Rich markup escaping**: any string that originates from user input or an external process (tool output, MCP error text, saved prompt content, etc.) must be passed through `rich.markup.escape()` before being interpolated into an f-string handed to `console.print`, otherwise it can be parsed as rich markup. Existing code consistently does this — follow the same pattern for new print sites.
