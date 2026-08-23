"""Unit tests for the SSE heartbeat interleaver (audit B3).

The original implementation kept completed tasks in its ``pending`` set, so
``asyncio.wait(FIRST_COMPLETED)`` returned immediately on stale done tasks
after the first event — an unbounded CPU-bound burst of heartbeats for the
entire stream. These tests pin the fixed behaviour: at most one ping per
interval, no bursts, and correct teardown under cancellation.
"""

import asyncio

import pytest

from src.http.routers.conversation import _HEARTBEAT_PING, _sse_with_heartbeat


@pytest.mark.asyncio
async def test_heartbeat_no_burst_and_pings_during_silence():
    async def gen():
        yield "event-1"
        await asyncio.sleep(0.15)  # silence longer than interval
        yield "event-2"

    pings: list[float] = []
    start = asyncio.get_running_loop().time()
    async for item in _sse_with_heartbeat(gen(), interval=0.05):
        if item == _HEARTBEAT_PING:
            pings.append(asyncio.get_running_loop().time() - start)
    assert len(pings) <= 4, f"hot-spin: {len(pings)} pings"  # was unbounded
    gaps = [b - a for a, b in zip(pings, pings[1:])]
    assert all(g >= 0.045 for g in gaps), f"burst detected: {gaps}"


@pytest.mark.asyncio
async def test_heartbeat_does_not_delay_upstream_events():
    """Events flow through immediately; heartbeats only fill silence."""
    events: list[str] = []
    start = asyncio.get_running_loop().time()

    async def gen():
        yield "a"
        yield "b"
        yield "c"

    async for item in _sse_with_heartbeat(gen(), interval=5.0):
        if item != _HEARTBEAT_PING:
            events.append(item)
    assert events == ["a", "b", "c"]
    assert asyncio.get_running_loop().time() - start < 1.0  # no idle waits


@pytest.mark.asyncio
async def test_heartbeat_stops_when_upstream_ends():
    async def gen():
        yield "only"

    count = 0
    async for item in _sse_with_heartbeat(gen(), interval=0.02):
        if item == _HEARTBEAT_PING:
            count += 1
    # Upstream ends immediately; at most one ping may fire before end is noticed.
    assert count <= 1


@pytest.mark.asyncio
async def test_heartbeat_propagates_upstream_exception():
    async def gen():
        yield "a"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        async for _ in _sse_with_heartbeat(gen(), interval=0.02):
            pass


@pytest.mark.asyncio
async def test_heartbeat_cancellation_is_not_swallowed():
    """Cancelling the outer consumer must propagate (not be eaten by teardown)."""

    async def gen():
        while True:
            yield "x"

    async def consume():
        async for _ in _sse_with_heartbeat(gen(), interval=0.02):
            await asyncio.sleep(0.01)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
