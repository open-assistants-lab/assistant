
import pytest

from src.config.settings import reload_settings
from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.middleware_summarization import SummarizationMiddleware
from src.sdk.native_tools import get_native_tools
from src.storage.paths import _paths_cache
from tests.integration.fake_provider import FakeProvider

TEST_PROMPT = (
    "You are Assistant, a helpful AI assistant. "
    "You have access to various tools. Use them when appropriate."
)


@pytest.fixture
def fake_provider():
    """Returns a FakeProvider with a safe default response."""
    return FakeProvider()


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    """Redirect data_root to tmp_path so filesystem tools use an isolated temp dir."""
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path))
    reload_settings()
    _paths_cache.clear()
    yield


@pytest.fixture
def loop(fake_provider, _isolated_paths):
    """Returns an AgentLoop with FakeProvider + all real middlewares + isolated tmp dir."""
    tools = get_native_tools()
    loop = AgentLoop(
        provider=fake_provider,
        tools=tools,
        system_prompt=TEST_PROMPT,
        middlewares=[
            SummarizationMiddleware(
                model="ollama-cloud:test",
                trigger=("tokens", 100),
                keep=("messages", 50),
            ),
        ],
        run_config=RunConfig(max_llm_calls=10),
    )
    return loop
