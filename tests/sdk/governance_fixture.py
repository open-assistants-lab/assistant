"""Test-only governed tool definitions; never imported by production registration."""

from src.sdk.tools import ExternalHTTPExecutor, ToolAnnotations, ToolDefinition


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
