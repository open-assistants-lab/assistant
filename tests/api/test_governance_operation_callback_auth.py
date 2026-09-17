"""Callback routes are capability-authenticated, not user-Bearer authenticated."""

from src.http.main import _is_operation_callback_path


def test_only_exact_operation_callback_paths_bypass_client_auth() -> None:
    assert _is_operation_callback_path("/governance/operations/op-1/events")
    assert _is_operation_callback_path("/governance/operations/op-1/complete")
    assert _is_operation_callback_path("/governance/operations/op-1/fail")
    assert not _is_operation_callback_path("/governance/operations/op-1")
    assert not _is_operation_callback_path("/governance/operations/op-1/cancel")
    assert not _is_operation_callback_path("/governance/operations/op-1/events/more")
