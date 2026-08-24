"""Async store access: to_thread offload, single-flight, bounded cache (audit S4)."""

import asyncio

import pytest

from src.storage.messages import (
    _MESSAGE_STORE_CACHE_MAX,
    _stores,
    aget_message_store,
    get_message_store,
)


@pytest.fixture(autouse=True)
def _clean_stores():
    _stores.clear()
    yield
    _stores.clear()


async def test_aget_message_store_offloads_construction_to_thread(monkeypatch, tmp_path):
    """First construction must run off the event loop (thread executor)."""
    from src import storage

    called_with: list[type] = []
    orig_to_thread = asyncio.to_thread

    def spy_to_thread(func, *args, **kwargs):
        called_with.append(func)
        return orig_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(storage.messages.asyncio, "to_thread", spy_to_thread)

    store = await aget_message_store("thread-user", workspace_id="personal")

    assert store is not None
    assert called_with and called_with[0].__name__ == "MessageStore"


async def test_aget_message_store_single_flight(monkeypatch, tmp_path):
    """Concurrent first-access must construct the store exactly once."""
    from src import storage

    to_thread_calls: list[type] = []
    orig_to_thread = asyncio.to_thread

    def spy_to_thread(func, *args, **kwargs):
        to_thread_calls.append(func)
        return orig_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(storage.messages.asyncio, "to_thread", spy_to_thread)

    async def _get():
        return await aget_message_store("single-user", workspace_id="personal")

    s1, s2 = await asyncio.gather(_get(), _get())

    assert s1 is s2
    # Single-flight: both concurrent callers share ONE off-thread construction.
    assert to_thread_calls.count(MessageStore) == 1


async def test_message_store_cache_is_bounded():
    """The in-process store cache must not grow unbounded with user ids."""
    for i in range(_MESSAGE_STORE_CACHE_MAX + 10):
        get_message_store(f"user-{i}", workspace_id="personal")

    assert len(_stores) <= _MESSAGE_STORE_CACHE_MAX


async def test_aget_message_store_reuses_cached_store():
    """Warm cache must not re-construct (fast path)."""
    store_a = await aget_message_store("cached-user", workspace_id="personal")
    store_b = await aget_message_store("cached-user", workspace_id="personal")
    assert store_a is store_b
