"""Phase 2 async governed-tool contracts."""

from __future__ import annotations

from src.sdk.governance import GovernanceService
from src.sdk.tools_custom import _parse_tool_file


def test_tool_annotations_default_to_synchronous_and_validate_async() -> None:
    from src.sdk.tools import ToolAnnotations

    assert ToolAnnotations().execution_mode == "sync"
    assert ToolAnnotations(execution_mode="async").execution_mode == "async"


def test_custom_tool_parses_async_execution_mode(tmp_path) -> None:
    tool_file = tmp_path / "TOOL.md"
    tool_file.write_text(
        """---
name: menu_change_execute
description: Durable menu change
command: echo {{store}}
annotations:
  requires_approval: true
  execution_mode: async
---
""",
        encoding="utf-8",
    )

    definition = _parse_tool_file(tool_file)

    assert definition is not None
    assert definition.annotations.requires_approval is True
    assert definition.annotations.execution_mode == "async"


def test_async_approval_consumes_pending_and_returns_same_operation(tmp_path) -> None:
    service = GovernanceService(data_root=str(tmp_path))
    proposal_id = service.create_pending(
        "alice", "menu_change_execute", {"store": "HQ"}, tier="explicit"
    )

    operation, accepted_now = service.approve_async_operation("alice", proposal_id)
    repeated, accepted_again = service.approve_async_operation("alice", proposal_id)

    assert accepted_now is True
    assert accepted_again is False
    assert operation.operation_id == repeated.operation_id
    assert operation.status.value == "queued"
    assert service.get_pending("alice", proposal_id)["status"] == "consumed"


def test_operation_listing_filters_by_status_and_cancel_is_durable(tmp_path) -> None:
    service = GovernanceService(data_root=str(tmp_path))
    first = service.create_pending("alice", "menu_change_execute", {}, tier="explicit")
    second = service.create_pending("alice", "menu_change_execute", {}, tier="explicit")
    first_operation, _ = service.approve_async_operation("alice", first)
    second_operation, _ = service.approve_async_operation("alice", second)
    assert service.operations.request_cancel("alice", second_operation.operation_id)

    queued = service.list_operations("alice", status="queued")
    cancelled = service.list_operations("alice", status="cancelled")

    assert [operation.operation_id for operation in queued] == [first_operation.operation_id]
    assert [operation.operation_id for operation in cancelled] == [second_operation.operation_id]
    cancelled_request = service.request_operation_cancel("alice", second_operation.operation_id)
    assert cancelled_request.cancel_requested is True
