import pytest

from src.sdk.execution_models import Outcome
from src.sdk.execution_store import SQLiteReceiptStore
from src.sdk.loop import AgentLoop
from src.sdk.messages import ToolCall
from src.sdk.tools import ToolDefinition, ToolResult


class StubProvider:
    provider_id = "stub"
    model = "stub-model"


@pytest.mark.asyncio
async def test_shell_tool_execution_is_receipted_without_changing_result(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")

    async def shell(command: str) -> ToolResult:
        return ToolResult(content=f"ran: {command}")

    tool = ToolDefinition(
        name="shell_execute",
        description="test shell",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        function=shell,
    )
    loop = AgentLoop(
        provider=StubProvider(),
        tools=[tool],
        user_id="test-user",
        execution_store=store,
    )

    result = await loop._execute_tool(
        ToolCall(id="call-1", name="shell_execute", arguments={"command": "echo hi"})
    )

    assert result.content == "ran: echo hi"
    assert result.is_error is False
    receipt = await store.get_by_request("call-1")
    assert receipt is not None
    assert receipt.outcome is Outcome.SUCCEEDED
    assert receipt.content["content"] == "ran: echo hi"
    await store.close()
