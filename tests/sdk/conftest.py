"""SDK conftest - shared fixtures for SDK tests."""

import os
import tempfile
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def reset_settings():
    """Clear cached settings and DEPLOYMENT_* env vars before each SDK test.

    Prevents cross-test pollution from session-scoped fixtures that
    set DEPLOYMENT_EA_ROOT / DEPLOYMENT_DATA_PATH.
    """
    saved = {}
    for var in (
        "DEPLOYMENT_EA_ROOT",
        "DEPLOYMENT_DATA_PATH",
        "DEPLOYMENT_DATA_ROOT",
        "DEPLOYMENT_MODE",
    ):
        # DEPLOYMENT_DATA_ROOT too: tests/api's session-scoped fixture sets it
        # and its teardown only runs at session end, so sdk tests inherit it
        # (long tmp root inflated the skills-context header budget and broke
        # the description-budget assertions — full-suite order dependence).
        saved[var] = os.environ.pop(var, None)
    from src.config.settings import reload_settings
    from src.storage.paths import _paths_cache

    reload_settings()
    _paths_cache.clear()
    yield
    _paths_cache.clear()
    # Setup deliberately loads settings with SDK-test environment isolation.
    # Teardown must clear, rather than reload, that singleton so subsequent
    # non-SDK tests retain control over when configuration is initialized.
    from src.config import settings as settings_module

    settings_module._config = None
    for var, val in saved.items():
        if val is not None:
            os.environ[var] = val


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for per-test isolation."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_llm_response():
    """Create a mock LLM response (LangChain AIMessage format)."""

    def _make(content="", tool_calls=None):
        msg = MagicMock()
        msg.content = content
        msg.type = "ai"
        msg.tool_calls = tool_calls or []
        msg.additional_kwargs = {}
        msg.response_metadata = {}
        msg.id = "msg_test"
        return msg

    return _make


@pytest.fixture
def mock_tool_call():
    """Create a mock tool call."""

    def _make(name="time_get", args=None, call_id="call_1"):
        return {
            "name": name,
            "args": args or {},
            "id": call_id,
            "type": "tool_call",
        }

    return _make


@pytest.fixture
def mock_tool_result():
    """Create a mock tool result message (LangChain ToolMessage format)."""

    def _make(content="result", name="time_get", tool_call_id="call_1"):
        msg = MagicMock()
        msg.content = content
        msg.name = name
        msg.tool_call_id = tool_call_id
        msg.type = "tool"
        return msg

    return _make


@pytest.fixture(autouse=True)
async def cleanup_work_queue_cache():
    """Prevent cross-test work_queue DB cache contamination."""
    yield
    from src.sdk.subagent_work_queue import _db_cache

    for cached_db in list(_db_cache.values()):
        await cached_db.close()
    _db_cache.clear()
