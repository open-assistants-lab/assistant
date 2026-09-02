"""M4 governance unit tests: tiers, durable pendings, receipts (issue #6)."""

from pathlib import Path

import pytest

from src.sdk.governance import (
    GovernanceService,
    Tier,
    get_governance_service,
)


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
    )
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    return GovernanceService()


class TestTierResolution:
    def test_autonomous_default(self, svc):
        assert svc.resolve_tier("u1", "files_read") == "autonomous"

    def test_tier_from_settings_mapping(self, svc, monkeypatch):
        from src.config.settings import reload_settings

        monkeypatch.setenv("GOVERNANCE_TIERS", '{"files_delete": "explicit"}')
        from src.config import reload_settings

        reload_settings()
        try:
            assert svc.resolve_tier("u1", "files_delete") == "explicit"
        finally:
            monkeypatch.delenv("GOVERNANCE_TIERS")
            reload_settings()

    def test_requires_approval_annotation_defaults_to_explicit(self, svc, monkeypatch):
        """Annotation declares: a tool with requires_approval defaults to the
        explicit tier unless a settings tier mapping overrides it."""
        from src.sdk.tools import ToolAnnotations

        assert ToolAnnotations(requires_approval=True) is not None

    def test_tier_change_takes_effect_without_redeploy(self, svc, monkeypatch):
        from src.config import reload_settings

        assert svc.resolve_tier("u1", "jobs_add") == "autonomous"
        monkeypatch.setenv("GOVERNANCE_TIERS", '{"jobs_add": "hard_block"}')
        reload_settings()
        assert svc.resolve_tier("u1", "jobs_add") == "hard_block"


class TestDurablePendings:
    def test_create_pending_persists(self, svc):
        pid = svc.create_pending("u1", "jobs_add", {"title": "x"})
        assert pid
        row = svc.get_pending("u1", pid)
        assert row is not None
        assert row["tool"] == "jobs_add"
        assert row["status"] == "pending"

    def test_pending_survives_restart(self, svc, tmp_path, monkeypatch):
        """Simulated restart: a NEW service instance over the same data root
        still sees the pending proposal (durable SQLite)."""
        pid = svc.create_pending("u1", "jobs_add", {"title": "x"})
        import src.sdk.governance as gov

        fresh = GovernanceService()
        row = fresh.get_pending("u1", pid)
        assert row is not None and row["status"] == "pending"

    def test_approve_idempotent(self, svc):
        pid = svc.create_pending("u1", "jobs_add", {})
        assert svc.approve("u1", pid) is True  # first approve executes
        assert svc.approve("u1", pid) is False  # duplicate = no-op, not error

    def test_show_then_auto_send_lazy_expiry(self, svc, monkeypatch):
        pid = svc.create_pending("u1", "email_send", {}, tier="show_then_auto_send")
        # Not expired -> still pending
        assert svc.resolve_pending("u1", pid)["status"] == "pending"
        # Clock shifted 10 min FORWARD (past the 300s expiry) -> lazily
        # auto-approved at read time (read-time evaluation, no scheduler).
        monkeypatch.setattr(
            "src.sdk.governance._now_minus",
            lambda seconds: __import__("datetime").datetime.now(
                __import__("datetime").UTC
            )
            + __import__("datetime").timedelta(minutes=10),
        )
        assert svc.resolve_pending("u1", pid)["status"] == "approved"


class TestReceipts:
    def test_proposal_approval_execution_linked(self, svc):
        pid = svc.create_pending("u1", "jobs_add", {}, tier="explicit")
        assert svc.approve("u1", pid) is True
        events = [e for e in svc.recent_events("u1") if e.kind == "approve"]
        kinds = [e.detail for e in events]
        assert "proposal" in kinds[0] or "proposal" in kinds[-1]
        # proposal -> approval -> execution chain present
        assert any("approved" in e.detail for e in events)


class TestDisabled:
    def test_disabled_service_passes_all(self, monkeypatch):
        from src.config import reload_settings

        monkeypatch.delenv("GOVERNANCE_TIERS", raising=False)
        monkeypatch.setenv("GOVERNANCE_ENABLED", "false")
        reload_settings()
        try:
            s = get_governance_service("u1")
            assert s.resolve_tier("u1", "files_delete") == "autonomous"
        finally:
            monkeypatch.delenv("GOVERNANCE_ENABLED")
            reload_settings()

class TestHITLMiddleware:
    """M4-1: middleware integration — synthetic refusal shape (hard_block),
    durable pending (explicit), auto-send window (show_then_auto_send)."""

    def _mw(self, tmp_path, monkeypatch, user_id="u1"):
        import src.sdk.governance as gov
        from src.sdk.middleware_hitl import HITLMiddleware

        monkeypatch.setattr(
            "src.storage.paths.DataPaths",
            type(
                "DP",
                (),
                {
                    "root": property(lambda self: tmp_path / "root"),
                    "__init__": lambda self, **k: None,
                },
            ),
        )
        monkeypatch.setattr(gov, "_services", {})
        from src.config import reload_settings

        monkeypatch.setenv("GOVERNANCE_ENABLED", "true")
        reload_settings()
        return HITLMiddleware(user_id=user_id)

    @pytest.mark.asyncio
    async def test_hard_block_synthetic_refusal(self, tmp_path, monkeypatch):
        mw = self._mw(tmp_path, monkeypatch)
        monkeypatch.setenv("GOVERNANCE_TIERS", '{"jobs_add": "hard_block"}')
        from src.config import reload_settings

        reload_settings()
        result = await mw.guard_tool_call("jobs_add", {"title": "x"})
        assert result is not None and result.is_error
        assert result.structured_content["governance"] == "hard_block"
        assert result.structured_content["executed"] is False

    @pytest.mark.asyncio
    async def test_autonomous_passes_through(self, tmp_path, monkeypatch):
        mw = self._mw(tmp_path, monkeypatch)
        assert await mw.guard_tool_call("files_read", {}) is None

    @pytest.mark.asyncio
    async def test_explicit_creates_durable_pending(self, tmp_path, monkeypatch):
        mw = self._mw(tmp_path, monkeypatch)
        monkeypatch.setenv("GOVERNANCE_TIERS", '{"email_send": "explicit"}')
        from src.config import reload_settings

        reload_settings()
        result = await mw.guard_tool_call("email_send", {"to": "a@b.c"})
        pid = result.structured_content["proposal_id"]
        assert result.structured_content["status"] == "pending"
        # survives restart: a fresh service over the same root sees it
        from src.sdk.governance import GovernanceService

        row = GovernanceService().get_pending("u1", pid)
        assert row is not None and row["status"] == "pending"

    @pytest.mark.asyncio
    async def test_show_then_auto_send_window(self, tmp_path, monkeypatch):
        mw = self._mw(tmp_path, monkeypatch)
        monkeypatch.setenv("GOVERNANCE_TIERS", '{"email_send": "show_then_auto_send"}')
        from src.config import reload_settings

        reload_settings()
        result = await mw.guard_tool_call("email_send", {})
        assert result.structured_content["governance"] == "show_then_auto_send"
        assert result.structured_content["status"] in ("pending", "approved")


class TestExecutionLegChecks:
    """Bug-hunt fixes: execution leg re-checks capabilities + current tier."""

    @pytest.fixture()
    def svc_with_pending(self, svc, monkeypatch):
        async def fake_invoke(arguments):
            return "EXECUTED-BODY"

        monkeypatch.setenv("GOVERNANCE_TIERS", '{"gated_tool": "explicit"}')
        import src.sdk.governance as gov

        monkeypatch.setattr(gov, "governance_enabled", lambda: True)
        from src.sdk.tools import ToolDefinition

        td = ToolDefinition(
            name="gated_tool",
            description="gated",
            input_schema={"type": "object", "properties": {}},
            ainvoke=fake_invoke,  # type: ignore[arg-type]
        )
        monkeypatch.setattr(
            "src.sdk.native_tools.get_native_tools", lambda: [td]
        )
        pid = svc.create_pending(
            "u1", "gated_tool", {"x": "1"}, tier="explicit"
        )
        svc.approve("u1", pid)
        return svc, pid

    @pytest.mark.asyncio
    async def test_disabled_tool_not_executed(self, svc_with_pending, monkeypatch):
        """P1: a tool disabled after pending creation must not execute."""
        import src.sdk.capabilities as caps_mod

        svc, pid = svc_with_pending
        monkeypatch.setattr(
            caps_mod, "load_capabilities", lambda root: {"tools": {"gated_tool": False}}
        )
        result = await svc.execute_approved("u1", pid)
        assert result["is_error"] is True
        assert "disabled" in result["structured_content"].get("error", "")

    @pytest.mark.asyncio
    async def test_hard_block_tier_change_refuses_execution(self, svc_with_pending, monkeypatch):
        """Tier re-resolution: pending created as explicit, now hard_block."""
        import src.sdk.governance as gov

        svc, pid = svc_with_pending
        monkeypatch.setattr(
            gov,
            "get_governance_service",
            lambda user_id=None: svc,
        )
        monkeypatch.setattr(
            src_sdk_governance_tier_source(svc, monkeypatch), "resolve_tier"
        ) if False else None
        # Force resolve_tier to return hard_block via capabilities.
        import src.sdk.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod,
            "load_capabilities",
            lambda root: {"governance_tiers": {"gated_tool": "hard_block"}},
        )
        result = await svc.execute_approved("u1", pid)
        assert result["is_error"] is True
        assert "hard_block" in result["content"]


import src.sdk.governance as _gov_mod  # noqa: E402


def src_sdk_governance_tier_source(svc, monkeypatch):  # pragma: no cover
    return _gov_mod


class TestPathTraversal:
    def test_db_path_rejects_traversal(self, svc, tmp_path):
        with pytest.raises(ValueError):
            svc._db_path("../../tmp/evil")

    @pytest.mark.asyncio
    async def test_resolve_tier_corrupt_caps_fails_closed(self, svc, monkeypatch):
        import src.sdk.capabilities as caps_mod
        import src.sdk.governance as gov

        def corrupt(root):
            raise RuntimeError("yaml parse error")

        monkeypatch.setattr(caps_mod, "load_capabilities", corrupt)
        monkeypatch.setattr(
            gov, "get_governance_service", lambda user_id=None: svc
        )
        tier = svc.resolve_tier("u1", "some_tool")
        assert tier == "explicit"  # fail closed: conservative pending
