"""Async store access: to_thread offload, single-flight, bounded cache (audit S4)."""

import asyncio
import threading

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
    import src.storage.messages as messages_mod

    construction_count = 0
    first_entered = threading.Event()
    release = threading.Event()

    orig_init = messages_mod.MessageStore.__init__

    def slow_init(self, user_id, base_dir=None, workspace_id="personal"):
        nonlocal construction_count
        construction_count += 1
        if construction_count == 1:
            # Block the worker thread until the second caller is also waiting,
            # then release. If the second caller were constructing (no single
            # flight), construction_count would exceed 1.
            first_entered.set()
            release.wait(timeout=5)
        orig_init(self, user_id, base_dir=base_dir, workspace_id=workspace_id)

    monkeypatch.setattr(messages_mod.MessageStore, "__init__", slow_init)

    async def _get():
        return await aget_message_store("single-user", workspace_id="personal")

    task1 = asyncio.create_task(_get())
    await asyncio.sleep(0)  # let task1 schedule its to_thread construction
    assert first_entered.wait(timeout=5), "first construction never started"
    task2 = asyncio.create_task(_get())
    await asyncio.sleep(0.05)  # give task2 time to (wrongly) construct
    release.set()
    s1, s2 = await asyncio.gather(task1, task2)

    assert s1 is s2
    assert construction_count == 1


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
