"""Capability-enforcement at lazy-load + prompt/allowlist alignment (audit E24-tools).

Security regression tests: a tool disabled via PATCH /tools/{name} scope=none
must be (a) refused at the execution boundary even if already registered or
present in the persisted tool index, (b) invisible to tool_search, and
(c) never advertised by prompts that reference shell commands.

Time is not frozen here; no network access is required.
"""

from __future__ import annotations

from typing import Any

from src.sdk.loop import AgentLoop
from src.sdk.messages import ToolCall
from src.sdk.tools import tool


def _make_loop(caps: dict[str, Any], tools: list | None = None) -> AgentLoop:
    """AgentLoop wired with a caps_check mirroring runner.py's threading."""

    def caps_check(name: str) -> bool:
        from src.sdk.capabilities import resource_enabled

        return resource_enabled(caps, "tools", name)

    return AgentLoop(
        provider=type("P", (), {})(),  # never called in these tests
        tools=tools or [],
        caps_check=caps_check,
    )


def _register(loop: AgentLoop, name: str = "files_delete") -> None:
    @tool
    def files_delete(path: str) -> str:
        """Delete a file."""
        return f"deleted {path}"

    files_delete.name = name
    loop._registry.register(files_delete)


class TestExecutionBoundaryCaps:
    def test_registered_tool_refused_when_disabled(self):
        """A mid-session scope change must block execution via the registry hit."""
        loop = _make_loop({"tools": {"files_delete": False}})
        _register(loop)
        result = asyncio_run_result(loop, "files_delete")
        assert result.is_error is True
        assert result.content.startswith("Tool is disabled")

    def test_lazy_load_refused_when_disabled(self):
        """_try_lazy_load must refuse BEFORE resolving/registering anything."""
        loop = _make_loop({"tools": {"files_delete": False}})

        class FakeIndex:
            def get_definition(self, name):
                raise AssertionError("index consulted for a disabled tool")

            def get_reconstruct(self, name):
                raise AssertionError("index consulted for a disabled tool")

            def get_tool_type(self, name):
                raise AssertionError("index consulted for a disabled tool")

        loop._tool_index = FakeIndex()

        async def run():
            return await loop._try_lazy_load(ToolCall(id="t1", name="files_delete", arguments={}))

        result = _run(run())
        assert result is not None and result.is_error is True
        assert result.content.startswith("Tool is disabled")
        # Nothing was registered into the live registry.
        assert loop._registry.get("files_delete") is None

    def test_enabled_tool_still_executes(self):
        caps: dict[str, Any] = {}
        loop = _make_loop(caps)
        _register(loop)

        async def run():
            return await loop._execute_tool(ToolCall(id="t1", name="files_delete", arguments={"path": "x"}))

        result = _run(run())
        assert result.is_error is False
        assert "deleted x" in result.content


def asyncio_run_result(loop: AgentLoop, name: str):
    async def run():
        return await loop._execute_tool(ToolCall(id="t1", name=name, arguments={"path": "x"}))

    return _run(run())


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class TestPromptAlignment:
    def test_guideline_never_names_disallowed_commands(self):
        """config.yaml allowlist excludes ls/rg/find — the guideline must not
        steer the model toward them (audit drift item)."""
        from src.sdk.runner import _get_file_ops_guideline

        caps: dict[str, Any] = {
            "tools": {"shell_execute": True, "files_glob_search": False, "files_grep_search": False}
        }
        text = _get_file_ops_guideline(caps)
        assert text  # guideline still present when search tools are off
        for banned in ("ls", "rg", "find"):
            assert f" {banned}" not in text and f"{banned}," not in text

    def test_tool_preferences_built_from_enabled_tools_only(self):
        """Preference lines referencing disabled tools must be omitted."""
        from src.sdk.runner import _build_tool_preferences

        caps: dict[str, Any] = {
            "tools": {
                "web_fetch": True,
                "web_search": False,
                "files_read": False,
                "files_list": False,
                "files_glob_search": False,
                "files_write": False,
                "files_grep_search": False,
                "shell_execute": True,
            }
        }
        prefs = _build_tool_preferences(caps)
        assert "web_fetch" in prefs
        assert "web_search" not in prefs
        assert "files_read" not in prefs
        assert "files_grep_search" not in prefs
        assert "shell_execute only for commands" in prefs


class TestResetGenerationGuard:
    def test_inflight_creation_discarded_after_reset(self):
        """A reset landing mid-creation must discard the stale-caps loop:
        get_sdk_loop retries internally and caches ONLY the post-reset loop."""
        import asyncio

        from src.sdk import runner as runner_mod

        created: list[str] = []
        markers: list[int] = []
        reset_fired = False

        async def fake_create(user_id, workspace_id, model=None, provider_keys=None,
                              session_id=None):
            nonlocal reset_fired
            created.append(session_id or "")
            from types import SimpleNamespace
            marker = len(markers)
            markers.append(marker)
            # Simulate ONE reset landing mid-creation (the real-world race:
            # PATCH saves caps + resets while a session loop materializes).
            if not reset_fired:
                reset_fired = True
                runner_mod.reset_user_sdk_loops(user_id, reason="test-reset-midflight")
            return SimpleNamespace(marker=marker)

        async def scenario():
            runner_mod._loop_cache.clear()
            orig_create = runner_mod.create_sdk_loop
            runner_mod.create_sdk_loop = fake_create  # type: ignore[assignment]
            try:
                loop = await runner_mod.get_sdk_loop("u1", session_id="s1")
            finally:
                runner_mod.create_sdk_loop = orig_create  # type: ignore[assignment]

            # Superseded attempt + internal retry both ran.
            assert created == ["s1", "s1"]
            assert markers == [0, 1]
            # Only ONE u1 loop cached — the POST-reset one (marker=1).
            u1_entries = [
                v for k, v in runner_mod._loop_cache.items() if k.startswith("u1:")
            ]
            assert len(u1_entries) == 1
            assert getattr(u1_entries[0], "marker") == 1
            assert loop is u1_entries[0]
            # The stale pre-reset loop (marker=0) never entered any cache slot.
            all_markers = [getattr(v, "marker", None) for v in runner_mod._loop_cache.values()]
            assert 0 not in all_markers

        asyncio.run(scenario())
