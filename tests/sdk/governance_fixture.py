"""Test-only governed tool definitions; never imported by production registration."""

from pathlib import Path

from src.sdk.tools import ExternalHTTPExecutor, ToolAnnotations, ToolDefinition, ToolResult


def local_fixture(target: Path) -> ToolDefinition:
    async def write_payload(payload: str, user_id: str = "default_user") -> ToolResult:
        target.write_text(payload, encoding="utf-8")
        return ToolResult(
            content="Fixture applied",
            structured_content={"result_id": "fixture-1", "actor": user_id},
        )

    return ToolDefinition(
        name="governance_local_fixture",
        description="Write only a test-owned fixture file.",
        parameters={
            "type": "object", "properties": {
                "payload": {"type": "string"}, "user_id": {"type": "string"},
            },
            "required": ["payload"],
        },
        annotations=ToolAnnotations(read_only=False, destructive=True, requires_approval=True),
        function=write_payload,
    )


def external_fixture() -> ToolDefinition:
    def refuse_in_process(**kwargs):
        raise AssertionError("external fixture must only use governed dispatch")

    return ToolDefinition(
        name="governance_fixture",
        description="Dispatch a synthetic fixture action, not a vendor operation.",
        parameters={
            "type": "object",
            "properties": {"payload": {"type": "string"}},
            "required": ["payload"],
        },
        annotations=ToolAnnotations(
            read_only=False,
            destructive=True,
            requires_approval=True,
            execution_mode="async",
            executor=ExternalHTTPExecutor(
                kind="external_http",
                dispatch_url="https://executor.invalid/fixture",
                manifest_hash="fixture-v1",
            ),
        ),
        function=refuse_in_process,
    )
