"""Issue #20 regression: middleware registration must be reachable and independent.

The v0.6.2 fix for issue #18 accidentally left the SummarizationMiddleware and
HITLMiddleware appends *after* the ``_prune_context`` helper's return, making
them unreachable — so every production loop shipped with an empty middleware
list and approval-gated tools executed without governance.

Contract:
- summarization enabled  → SummarizationMiddleware registered
- governance enabled     → HITLMiddleware registered (independent of summarization)
- both disabled          → neither registered
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeIndex:
    """Mirrors the ToolIndex methods the runner touches (the runner re-indexes
    on row presence, not a row count)."""

    def __init__(self):
        self._names: set[str] = set()

    def count(self):
        return len(self._names)

    def clear(self):
        self._names.clear()

    def index_tool(self, td, *args, **kwargs):
        self._names.add(td.name)

    def list_all_names(self):
        return sorted(self._names)


def _settings(summarization_enabled: bool, governance_enabled: bool) -> MagicMock:
    settings = MagicMock()
    settings.memory.summarization.enabled = summarization_enabled
    settings.memory.summarization.get_trigger.return_value = ("messages", 2)
    settings.memory.summarization.get_keep.return_value = ("messages", 1)
    settings.memory.summarization.model = None
    settings.memory.summarization.trim_tokens_to_summarize = 4000
    settings.memory.summarization.prompt_file = None
    settings.verification.enabled = False
    settings.langfuse.enabled = False
    settings.governance.enabled = governance_enabled
    settings.governance.tiers = {}
    settings.governance.auto_send_expiry_seconds = 300
    return settings


async def _build_loop(
    monkeypatch: pytest.MonkeyPatch,
    *,
    summarization_enabled: bool,
    governance_enabled: bool,
    user_id: str = "mw_reg_user",
):
    from src.sdk import runner

    monkeypatch.setattr(
        runner, "get_settings", lambda: _settings(summarization_enabled, governance_enabled)
    )
    monkeypatch.setattr(runner, "get_native_tools", lambda: [])
    monkeypatch.setattr(runner, "_seed_default_workspace", lambda: None)
    monkeypatch.setattr(runner, "_get_system_prompt", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings", lambda user_id: None
    )
    monkeypatch.setattr(
        "src.sdk.tool_index.get_or_create_index",
        lambda *args, **kwargs: (_FakeIndex(), lambda: None),
    )
    # Governance enablement: the runner consults the existing helper at
    # loop-creation time; tests patch src.sdk.governance.governance_enabled.
    monkeypatch.setattr(
        "src.sdk.governance.governance_enabled",
        lambda: governance_enabled,
    )
    # Governance tier source: the runner wires HITLMiddleware based on
    # settings.governance.enabled; the middleware itself reads tiers via the
    # governance service — patch its construction site to keep the test
    # loop free of a real store.
    monkeypatch.setattr(
        "src.sdk.runner._load_user_capabilities", lambda user_id: {}
    )

    provider = AsyncMock()
    provider.provider_id = "openai"
    provider.model = "gpt-4.1"
    monkeypatch.setattr(runner, "get_cached_model_provider", lambda *a, **kw: provider)
    return await runner.create_sdk_loop(user_id=user_id, session_id="chat-1")


@pytest.mark.asyncio
async def test_enabled_summarization_registers_summary_middleware(monkeypatch):
    from src.sdk.middleware_summarization import SummarizationMiddleware

    loop = await _build_loop(monkeypatch, summarization_enabled=True, governance_enabled=False)
    assert any(isinstance(mw, SummarizationMiddleware) for mw in loop.middlewares)
    assert not any(type(mw).__name__ == "HITLMiddleware" for mw in loop.middlewares)


@pytest.mark.asyncio
async def test_enabled_governance_registers_hitl_without_summarization(monkeypatch):
    loop = await _build_loop(monkeypatch, summarization_enabled=False, governance_enabled=True)
    assert any(type(mw).__name__ == "HITLMiddleware" for mw in loop.middlewares)
    assert not any(
        type(mw).__name__ == "SummarizationMiddleware" for mw in loop.middlewares
    )


@pytest.mark.asyncio
async def test_disabled_features_register_neither(monkeypatch):
    loop = await _build_loop(monkeypatch, summarization_enabled=False, governance_enabled=False)
    middlewares = loop.middlewares
    assert not any(
        type(mw).__name__ in ("HITLMiddleware", "SummarizationMiddleware")
        for mw in middlewares
    )


@pytest.mark.asyncio
async def test_both_enabled_register_both(monkeypatch):

    loop = await _build_loop(monkeypatch, summarization_enabled=True, governance_enabled=True)
    types = {type(mw).__name__ for mw in loop.middlewares}
    assert "SummarizationMiddleware" in types
    assert "HITLMiddleware" in types
