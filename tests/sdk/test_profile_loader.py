"""Tests for main-agent-from-profile bootstrap (roadmap P0-T7 / K1).

Precedence rules under test (spec §4.5):
1. capabilities/scopes win over profile.tools
2. profile persona wins over user_prompt_set text
3. absent fields fall back to settings-derived behavior; no PROFILE.md = today
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agentprofile import AgentProfile, dumps_profile

from src.sdk import profile_loader, runner


def _write_profile(path, profile: AgentProfile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_profile(profile), encoding="utf-8")


def _profile(**overrides) -> AgentProfile:
    kwargs = dict(
        name="test-agent",
        description="test",
        model="anthropic:claude-sonnet-4-5",
        system_prompt="You are a meticulous legal drafter.",
        skills=["legal-style"],
        tools=["files_read"],
        max_llm_calls=7,
        cost_limit_usd=2.5,
        timeout_seconds=111,
    )
    kwargs.update(overrides)
    return AgentProfile(**kwargs)


# ---------------------------------------------------------------------------
# load_main_agent_profile
# ---------------------------------------------------------------------------


def test_load_returns_none_when_no_profile(tmp_path):
    assert profile_loader.load_main_agent_profile("u1", data_root=tmp_path) is None


def test_load_reads_user_level_profile_md(tmp_path):
    from src.storage.paths import DataPaths

    dp = DataPaths(user_id="u1", data_root=str(tmp_path))
    _write_profile(dp.main_agent_profile_path, _profile())
    profile = profile_loader.load_main_agent_profile("u1", data_root=tmp_path)
    assert profile is not None
    assert profile.name == "test-agent"
    assert profile.model == "anthropic:claude-sonnet-4-5"


def test_load_uses_data_paths_per_user_layout(monkeypatch, tmp_path):
    """data_root override must flow through DataPaths (Users/{id} layout)."""
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path))
    from src.storage.paths import DataPaths, _paths_cache

    _paths_cache.clear()
    dp = DataPaths(user_id="u9")
    _write_profile(dp.main_agent_profile_path, _profile())
    profile = profile_loader.load_main_agent_profile("u9")
    assert profile is not None and profile.name == "test-agent"
    _paths_cache.clear()


# ---------------------------------------------------------------------------
# build_loop_from_profile validation
# ---------------------------------------------------------------------------


def test_build_rejects_unknown_skill(monkeypatch, tmp_path):
    import src.skills.registry as skills_registry

    class FakeRegistry:
        def get_skill(self, name):
            return None

    monkeypatch.setattr(skills_registry, "get_skill_registry", lambda **kw: FakeRegistry())
    with pytest.raises(profile_loader.ProfileError, match="Unknown skill"):
        profile_loader.build_loop_from_profile(
            "u1", _profile(), data_root=tmp_path
        )


def test_build_rejects_unknown_tool(monkeypatch, tmp_path):
    import src.skills.registry as skills_registry
    from src.sdk.native_tools import get_native_tools

    class FakeRegistry:
        def get_skill(self, name):
            return object()

    monkeypatch.setattr(skills_registry, "get_skill_registry", lambda **kw: FakeRegistry())
    monkeypatch.setattr("src.sdk.native_tools.get_native_tools", lambda: get_native_tools())
    with pytest.raises(profile_loader.ProfileError, match="Unknown tool"):
        profile_loader.build_loop_from_profile(
            "u1", _profile(tools=["nonexistent_tool_xyz"]), data_root=tmp_path
        )


def test_build_requires_provider_key_for_cloud_model(monkeypatch, tmp_path):
    """Cloud providers must have a resolvable key at bootstrap — fail fast."""
    import src.skills.registry as skills_registry

    class FakeRegistry:
        def get_skill(self, name):
            return object()

    monkeypatch.setattr(skills_registry, "get_skill_registry", lambda **kw: FakeRegistry())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings", lambda user_id: None
    )
    with pytest.raises(profile_loader.ProfileError, match="API key"):
        profile_loader.build_loop_from_profile("u1", _profile(), data_root=tmp_path)


def test_build_local_provider_needs_no_key(monkeypatch, tmp_path):
    """Local ollama models are keyless by design."""
    import src.skills.registry as skills_registry

    class FakeRegistry:
        def get_skill(self, name):
            return object()

    monkeypatch.setattr(skills_registry, "get_skill_registry", lambda **kw: FakeRegistry())
    # No exception expected — validation passes without keys.
    profile_loader.build_loop_from_profile(
        "u1", _profile(model="ollama:minimax-m2.5"), data_root=tmp_path
    )


# ---------------------------------------------------------------------------
# create_sdk_loop wiring + precedence
# ---------------------------------------------------------------------------


@pytest.fixture
def loop_factory_patched(monkeypatch, tmp_path):
    """Reuse test_runner's harness shape but keep it self-contained here."""
    from src.sdk import runner as runner_mod

    settings = SimpleNamespace(
        memory=SimpleNamespace(
            summarization=SimpleNamespace(
                enabled=False,
                model=None,
                prompt_file=None,
                trim_tokens_to_summarize=4000,
                get_trigger=lambda: ("messages", 2),
                get_keep=lambda: ("messages", 1),
            )
        ),
        verification=SimpleNamespace(enabled=False),
        langfuse=SimpleNamespace(enabled=False, public_key="", secret_key="", host=""),
        agent=SimpleNamespace(model="ollama:minimax-m2.5"),
    )
    monkeypatch.setattr(runner_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(runner_mod, "get_native_tools", lambda: [])
    monkeypatch.setattr(runner_mod, "_seed_default_workspace", lambda: None)
    monkeypatch.setattr(runner_mod, "_get_system_prompt", lambda *a, **kw: "BASE")
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings", lambda user_id: None
    )

    class FakeIndex:
        def count(self):
            return 0

        def clear(self):
            pass

        def index_tool(self, *a, **kw):
            pass

    monkeypatch.setattr(
        "src.sdk.tool_index.get_or_create_index",
        lambda *a, **kw: (FakeIndex(), lambda: None),
    )
    provider = SimpleNamespace(provider_id="ollama", model="minimax-m2.5")
    monkeypatch.setattr(runner_mod, "get_cached_model_provider", lambda *a, **kw: provider)
    return runner_mod


def _stub_skills_ok(monkeypatch):
    import src.skills.registry as skills_registry

    class FakeRegistry:
        def get_skill(self, name):
            return object()

    monkeypatch.setattr(skills_registry, "get_skill_registry", lambda **kw: FakeRegistry())


@pytest.mark.asyncio
async def test_create_sdk_loop_prefers_profile_model_and_persona(
    loop_factory_patched, monkeypatch, tmp_path
):
    _stub_skills_ok(monkeypatch)
    r = loop_factory_patched
    captured: dict = {}

    real_validate = profile_loader.validate_model_reference

    def spy(model_id, **kw):
        captured["model"] = model_id
        return real_validate(model_id)

    monkeypatch.setattr(profile_loader, "validate_model_reference", spy)
    _write_profile(
        tmp_path / "PROFILE.md",
        _profile(model="ollama:custom-model", system_prompt="PROFILE PERSONA"),
    )
    monkeypatch.setattr(
        profile_loader,
        "load_main_agent_profile",
        lambda user_id, data_root=None: _profile(
            model="ollama:custom-model", system_prompt="PROFILE PERSONA"
        ),
    )

    loop = await r.create_sdk_loop(user_id="u1")

    # profile.model wins over settings default (provider mock echoes request)
    assert captured["model"].endswith("custom-model")
    # profile persona wins over base/user-prompt text
    assert "PROFILE PERSONA" in (loop.system_prompt or "")
    # limits carried on RunConfig
    rc = loop.run_config
    assert rc.max_llm_calls == 7
    assert rc.cost_limit_usd == 2.5
    assert getattr(loop, "profile_timeout_seconds") == 111


@pytest.mark.asyncio
async def test_create_sdk_loop_no_profile_unchanged(loop_factory_patched, monkeypatch):
    called = []
    monkeypatch.setattr(
        profile_loader,
        "load_main_agent_profile",
        lambda user_id, data_root=None: called.append(user_id) or None,
    )
    r = loop_factory_patched
    loop = await r.create_sdk_loop(user_id="u1")
    assert called == ["u1"]
    assert (loop.system_prompt or "").startswith("BASE")
    assert loop.run_config.max_llm_calls != 7  # settings-derived defaults intact


# ---------------------------------------------------------------------------
# revalidate_and_reset: loop reset + WS session detach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalidate_resets_loops_and_detaches_sessions(monkeypatch):
    from src.sdk.session_worker import SessionWorkerRegistry

    registry = SessionWorkerRegistry()
    lock = await registry.acquire("u7::sess-1")
    assert lock is not None

    reset_calls = []
    monkeypatch.setattr(
        "src.sdk.runner.reset_user_sdk_loops",
        lambda user_id, reason=None: reset_calls.append(user_id) or 3,
    )

    result = await profile_loader.revalidate_and_reset(
        "u7", data_root="/nonexistent-p0-t7", registry=registry
    )

    assert reset_calls == ["u7"]
    assert result["detached_sessions"] == ["u7::sess-1"]
    # E26 semantics: the run's own finally releases the lock; profile swap
    # only requests cancellation so no stale loop serves an approved turn.
    assert lock.cancelled
    assert result["profile_found"] is False  # /nonexistent has no PROFILE.md
