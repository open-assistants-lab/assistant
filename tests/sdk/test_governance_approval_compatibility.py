"""Regression coverage for sync governance approval compatibility."""

from __future__ import annotations

from types import SimpleNamespace

from src.sdk.governance import GovernanceService


def test_sync_pending_does_not_resolve_active_tool_or_custom_paths(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))

    def unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("sync proposal must not resolve active tool metadata")

    monkeypatch.setattr(service, "external_executor_for_tool", unexpected_lookup)

    proposal_id = service.create_pending("alice", "sync_domain_tool", {"id": "1"})

    assert service.get_pending("alice", proposal_id)["executor"] is None


def test_active_tool_definition_supports_legacy_one_argument_custom_lookup(monkeypatch):
    from src.sdk import runner

    custom = SimpleNamespace(name="custom_domain_tool")
    calls: list[str] = []

    def legacy_lookup(user_id: str):
        calls.append(user_id)
        return [custom]

    monkeypatch.setattr(runner, "get_custom_tools", legacy_lookup, raising=False)
    monkeypatch.setattr("src.sdk.tools_custom.get_custom_tools", legacy_lookup)
    monkeypatch.setattr(runner, "get_native_tools", lambda: [])

    assert runner.get_active_tool_definition("alice", "custom_domain_tool") is custom
    assert calls == ["alice"]
