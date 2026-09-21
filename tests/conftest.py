"""Shared fixtures.

ChatCLI resolves ~/.chatcli when its modules are imported, so the home directory is
pointed at a throwaway folder *before* anything from `chatcli` is imported. A guard
then refuses to run if that didn't take effect, so a test can never touch (or wipe)
the real config and history.
"""

import io
import os
import shutil
import tempfile
from pathlib import Path

_TEST_HOME = Path(tempfile.mkdtemp(prefix="chatcli-tests-"))
os.environ["HOME"] = os.environ["USERPROFILE"] = str(_TEST_HOME)

import pytest
from cryptography.fernet import Fernet
from rich.prompt import Prompt

from chatcli import paths
from chatcli.models import Endpoint, Session, SystemPrompt
from chatcli.ui.common import console

if _TEST_HOME not in paths.APP_DIR.parents:
    pytest.exit(f"Refusing to run: {paths.APP_DIR} is outside the test home {_TEST_HOME}", returncode=2)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_HOME, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_app_dir():
    """Every test starts with an empty ~/.chatcli."""
    shutil.rmtree(paths.APP_DIR, ignore_errors=True)
    yield


@pytest.fixture
def fernet() -> Fernet:
    # A random key avoids the deliberately slow PBKDF2 step; crypto.py has its own tests.
    return Fernet(Fernet.generate_key())


@pytest.fixture
def out():
    """Capture everything printed through the shared rich console."""
    buf = io.StringIO()
    old_file, old_width = console.file, console.width
    console.file, console.width = buf, 110
    yield buf
    console.file, console.width = old_file, old_width


@pytest.fixture
def prompts(monkeypatch):
    """Script `Prompt.ask` answers: `prompts("1", "0")`. Fails loudly if the app asks for more."""
    def script(*answers):
        it = iter(answers)

        def ask(*args, **kwargs):
            try:
                answer = next(it)
            except StopIteration:
                raise AssertionError(f"unexpected extra prompt: {args!r}") from None
            if isinstance(answer, BaseException):
                raise answer          # e.g. KeyboardInterrupt to simulate Ctrl-C
            return answer

        monkeypatch.setattr(Prompt, "ask", ask)
        return it
    return script


@pytest.fixture
def endpoint() -> Endpoint:
    return Endpoint(name="ep", base_url="http://x/v1", api_key="KEY", model="m")


@pytest.fixture
def system_prompt() -> SystemPrompt:
    return SystemPrompt(name="P", content="be brief")


@pytest.fixture
def session() -> Session:
    return Session(
        name="chat", endpoint_name="ep", base_url="http://x/v1", model="m",
        prompt_name="P", system_prompt="be brief", api_key="KEY",
        path=paths.HISTORY_DIR / "chat.json",
    )
