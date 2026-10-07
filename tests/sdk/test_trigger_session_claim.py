"""#151: trigger runs must take the session claim, like every other run.

A webhook/cron/file-change run used to execute on the same cached AgentLoop as
a live interactive turn with no mutual exclusion: state was replaced under the
running turn, and the trigger's ``finally`` unregistered the live run's loop so
steering degraded to a follow-up turn.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.sdk import runner as _runner
from src.sdk.messages import Message
from src.sdk.session_worker import SessionBusyError, get_session_registry, session_key


class _FakeLoop:
    """The smallest object run_sdk_agent() drives."""

    def __init__(self, record: list[tuple[str, int]]) -> None:
        from src.sdk.state import AgentState

        self.state = AgentState(messages=[Message.assistant(content="ok")])
        self.rubric = None
        self.model_id = "test:model"
        self.provider = SimpleNamespace(model="test:model")
        self._record = record
        self.active = 0
        self.max_active = 0

    async def run(self, messages: list[Message]) -> list[Message]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self._record.append((str(messages[-1].content), self.active))
        try:
            await asyncio.sleep(0.05)
        finally:
            self.active -= 1
        return [Message.assistant(content="ok")]


def _install_fake_run(monkeypatch, loop: _FakeLoop) -> None:
    async def fake_get_sdk_loop(*args, **kwargs):
        return loop

    async def fake_run_with_verification(
        loop_arg, messages, user_id, session_id, rubric=None, model=None, **kwargs
    ):
        ran = await loop_arg.run(messages)
        return SimpleNamespace(attempts=[SimpleNamespace(messages=ran)])

    async def fake_persist_outcome(*args, **kwargs):
        return None

    monkeypatch.setattr(_runner, "get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr(_runner, "run_with_verification", fake_run_with_verification)
    monkeypatch.setattr(_runner, "_persist_run_outcome", fake_persist_outcome)


@pytest.mark.asyncio
async def test_trigger_run_waits_for_the_live_session_claim(monkeypatch, tmp_path):
    """A trigger run must queue behind the interactive run, not run beside it."""
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path))
    registry = get_session_registry()
    key = session_key("u-claim", "chat-1")
    await registry.acquire(key)  # the interactive run holds the session
    ran: list[tuple[str, int]] = []
    _install_fake_run(monkeypatch, _FakeLoop(ran))

    task = asyncio.create_task(
        _runner.run_sdk_agent(
            user_id="u-claim",
            messages=[Message.user("webhook turn")],
            session_id="chat-1",
        )
    )
    try:
        await asyncio.sleep(0.2)
        assert ran == [], (
            "the trigger run executed while another run held the session claim"
        )
    finally:
        await registry.release(key)

    await asyncio.wait_for(task, timeout=5)
    assert [entry[0] for entry in ran] == ["webhook turn"]
    assert not registry.holds(key), "the finished trigger run leaked the session claim"


@pytest.mark.asyncio
async def test_two_trigger_runs_on_one_session_serialize(monkeypatch, tmp_path):
    """The loop-3 spec's own test: triggers for one session queue correctly."""
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path))
    ran: list[tuple[str, int]] = []
    loop = _FakeLoop(ran)
    _install_fake_run(monkeypatch, loop)

    await asyncio.gather(
        _runner.run_sdk_agent(
            user_id="u-serial", messages=[Message.user("first")], session_id="chat-2"
        ),
        _runner.run_sdk_agent(
            user_id="u-serial", messages=[Message.user("second")], session_id="chat-2"
        ),
    )

    assert loop.max_active == 1, (
        f"two trigger runs overlapped on one AgentLoop (max concurrency {loop.max_active})"
    )
    assert sorted(entry[0] for entry in ran) == ["first", "second"]


@pytest.mark.asyncio
async def test_trigger_run_reports_busy_after_the_wait_budget(monkeypatch, tmp_path):
    """A stuck session must produce a clear error, never an unbounded hang."""
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path))
    monkeypatch.setattr(_runner, "TRIGGER_SESSION_WAIT_SECONDS", 0.2, raising=False)
    registry = get_session_registry()
    key = session_key("u-budget", "chat-3")
    await registry.acquire(key)
    ran: list[tuple[str, int]] = []
    _install_fake_run(monkeypatch, _FakeLoop(ran))

    try:
        with pytest.raises(SessionBusyError):
            await _runner.run_sdk_agent(
                user_id="u-budget",
                messages=[Message.user("webhook turn")],
                session_id="chat-3",
            )
        assert ran == [], "the run executed despite the session staying busy"
        assert registry.holds(key), "the waiting trigger dropped the holder's claim"
    finally:
        await registry.release(key)


def test_unregister_does_not_evict_another_runs_registration():
    """Registration is refcounted: a finishing trigger run may not unregister
    a loop that another (live) run still owns."""
    from src.sdk.runner import (
        get_user_loop,
        register_user_loop,
        unregister_user_loop,
    )

    loop = _FakeLoop([])
    register_user_loop("u-reg", loop, session_id="chat-4")  # interactive run
    register_user_loop("u-reg", loop, session_id="chat-4")  # trigger run, same loop
    unregister_user_loop("u-reg", loop, session_id="chat-4")  # trigger run finishes

    assert get_user_loop("u-reg", "chat-4") is loop, (
        "a finished trigger run evicted the live run's steering registration"
    )

    unregister_user_loop("u-reg", loop, session_id="chat-4")  # interactive run finishes
    assert get_user_loop("u-reg", "chat-4") is None
