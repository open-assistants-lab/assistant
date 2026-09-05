import pytest

from src.sdk.subagent_models import SubagentResult


@pytest.mark.asyncio
async def test_completion_bus_publishes_to_matching_subscribers():
    from src.sdk.subagent_completion import SubagentCompletion, completion_bus

    seen = []

    async def callback(event):
        seen.append(event)

    unsubscribe = completion_bus.subscribe("u", "s", callback)
    try:
        event = SubagentCompletion(
            user_id="u",
            workspace_id="personal",
            session_id="s",
            task_id="task-1",
            agent_name="worker",
            status="completed",
            result=SubagentResult(name="worker", task="t", success=True, output="done"),
        )
        await completion_bus.publish(event)
    finally:
        unsubscribe()

    assert seen == [event]


@pytest.mark.asyncio
async def test_run_service_completion_handler_steers_active_parent_loop(monkeypatch):
    from src.sdk.run_service import handle_subagent_completion
    from src.sdk.subagent_completion import SubagentCompletion

    steers = []

    class FakeLoop:
        def set_steer_sink(self, sink):
            self.sink = sink

        def steer(self, message):
            steers.append(message)
            self.sink(message)

    persisted = []

    class FakeStore:
        def add_message(self, role, content, metadata=None, session_id="default"):
            persisted.append((role, content, metadata, session_id))
            return "msg1"

    monkeypatch.setattr("src.sdk.run_service.get_user_loop", lambda user_id, session_id=None: FakeLoop())
    monkeypatch.setattr("src.sdk.run_service.aget_message_store", lambda user_id, workspace_id="personal": FakeAwaitable(FakeStore()))

    event = SubagentCompletion(
        user_id="u",
        workspace_id="personal",
        session_id="s",
        task_id="task-1",
        agent_name="worker",
        status="completed",
        result=SubagentResult(name="worker", task="t", success=True, output="finished output"),
    )

    await handle_subagent_completion(event)

    assert steers == ["Subagent 'worker' finished: finished output"]
    assert persisted == [("user", "Subagent 'worker' finished: finished output", {"steer": True, "subagent_completion": True, "task_id": "task-1"}, "s")]


@pytest.mark.asyncio
async def test_run_service_completion_handler_persists_active_steer_before_boundary(monkeypatch):
    from src.sdk.run_service import handle_subagent_completion
    from src.sdk.subagent_completion import SubagentCompletion

    class FakeLoop:
        def set_steer_sink(self, sink):
            self.sink = sink

        def steer(self, message):
            # Simulate a completion arriving during text generation: no tool
            # boundary drains the steer, so AgentLoop never calls the sink.
            self.message = message

    persisted = []

    class FakeStore:
        def add_message(self, role, content, metadata=None, session_id="default"):
            persisted.append((role, content, metadata, session_id))
            return "msg1"

    monkeypatch.setattr("src.sdk.run_service.get_user_loop", lambda user_id, session_id=None: FakeLoop())
    monkeypatch.setattr("src.sdk.run_service.aget_message_store", lambda user_id, workspace_id="personal": FakeAwaitable(FakeStore()))

    event = SubagentCompletion(
        user_id="u",
        workspace_id="personal",
        session_id="s",
        task_id="task-1",
        agent_name="worker",
        status="completed",
        result=SubagentResult(name="worker", task="t", success=True, output="finished output"),
    )

    await handle_subagent_completion(event)

    assert persisted == [("user", "Subagent 'worker' finished: finished output", {"steer": True, "subagent_completion": True, "task_id": "task-1"}, "s")]


@pytest.mark.asyncio
async def test_run_service_completion_handler_records_idle_followup(monkeypatch):
    from src.sdk.run_service import handle_subagent_completion
    from src.sdk.subagent_completion import SubagentCompletion

    persisted = []

    class FakeStore:
        def add_message(self, role, content, metadata=None, session_id="default"):
            persisted.append((role, content, metadata, session_id))
            return "msg1"

    monkeypatch.setattr("src.sdk.run_service.get_user_loop", lambda user_id, session_id=None: None)
    monkeypatch.setattr("src.sdk.run_service.aget_message_store", lambda user_id, workspace_id="personal": FakeAwaitable(FakeStore()))

    event = SubagentCompletion(
        user_id="u",
        workspace_id="personal",
        session_id="s",
        task_id="task-1",
        agent_name="worker",
        status="completed",
        result=SubagentResult(name="worker", task="t", success=True, output="finished output"),
    )

    await handle_subagent_completion(event)

    assert persisted == [("assistant", "Subagent 'worker' finished: finished output", {"subagent_completion": True, "task_id": "task-1", "status": "completed"}, "s")]


class FakeAwaitable:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def _inner():
            return self.value

        return _inner().__await__()
