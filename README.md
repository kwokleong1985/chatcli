# ChatCLI

A conversational command-line client for any OpenAI-compatible API, with encrypted credential storage, saved conversations and optional MCP tool calling.

## Features

- Works with any OpenAI-compatible endpoint (OpenAI, DeepSeek, local servers, etc.): set a name, base URL, API key and model.
- Endpoints, system prompts and MCP servers are stored in an encrypted config (`~/.chatcli/config.enc`), unlocked with a master password (PBKDF2 + Fernet).
- Named conversations are saved to `~/.chatcli/history`. API keys are never written to the history files.
- Reasoning controls: toggle thinking and set reasoning effort.
- Optional per-reply token and cache usage stats.
- MCP support: connect stdio MCP servers and let the model call their tools.
- Includes a ready-made Brave Search MCP server (`brave_search_mcp.py`) with web and news search.

## Requirements

- Python 3.10+
- Windows for the `.bat` launchers (the Python scripts themselves are not Windows-specific)

## Install

```bat
install.bat
```

or manually:

```bash
pip install -r requirements.txt
```

## Usage

```bat
chatcli.bat
```

or `python -m chatcli` from the repository root.

On first run you choose a master password. It encrypts your saved endpoints, so don't lose it: the config can't be recovered without it. From the main menu you can:

1. Add an endpoint (name, base URL, API key, model).
2. Add system prompts.
3. Add MCP servers.
4. Start or resume a conversation.

### In-chat commands

| Command | Description |
| --- | --- |
| `/info` | Show session details |
| `/system` | Show the system prompt |
| `/thinking on\|off` | Toggle thinking |
| `/effort <level>` | Set reasoning effort (`default`, `none`, `minimal`, `low`, `medium`, `high`, `xhigh`) |
| `/usage on\|off` | Show token/cache stats per reply |
| `/tools [on\|off]` | List or toggle MCP tools |
| `/clear` | Delete chat history (keeps settings) |
| `/quit` | Exit |

## Brave Search MCP server

1. Get an API key from [Brave Search API](https://brave.com/search/api/).
2. In ChatCLI, add an MCP server:
   - **Name:** `brave-search`
   - **Command:** `python`
   - **Arguments:** the full path to `brave_search_mcp.py`
   - **Env var:** `BRAVE_API_KEY` = your key
3. Use `/tools` in a chat to enable it.

Requests are throttled to about one per second to fit Brave's free plan. Set `BRAVE_MIN_INTERVAL` (seconds) to change that on a paid plan.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests run against a throwaway home directory, so they never touch your real `~/.chatcli`. They use a fake API client and, for the MCP tests, the bundled Brave server with a dummy key. Nothing goes over the network.

The code lives in the `chatcli/` package:

| Module | Responsibility |
| --- | --- |
| `app.py` | Entry point: unlock the config, run the main menu |
| `models.py` | Dataclasses for the config and conversations |
| `store.py`, `fileio.py` | Encrypted config and history files; crash-safe writes |
| `crypto.py`, `paths.py` | Master-password key derivation; file locations |
| `llm.py`, `providers.py` | The model call and tool-calling loop; provider-specific settings |
| `mcp_client.py` | Synchronous wrapper around stdio MCP servers |
| `ui/` | Menus, chat loop and slash commands (add a command with `@command`) |

## Security notes

- Config and logs live in `~/.chatcli`, outside this repository. Never commit that folder.
- The API error log (`~/.chatcli/logs/api_errors.jsonl`) may contain request details, so check it before sharing.

## License

[MIT](LICENSE)
