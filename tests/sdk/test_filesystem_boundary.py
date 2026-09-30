"""Filesystem boundary: allowed roots, unambiguous paths, and a repeat budget.

Three defects found by running the agent against a task whose answer was on
local disk (#46-adjacent investigation, 2026-09-28):

1. ``files_*`` hard-refused any absolute path outside the data root, so a task
   naming a real directory was unsatisfiable. The agent probed 32 times
   (``files_list`` x32) before falling back to the ungated ``shell_execute`` —
   the boundary was bypassed, just expensively.
2. ``files_list(path=".")`` resolves to the user data root, not the process
   cwd, and says "Empty directory: ." — a confident, wrong answer.
3. Nothing stopped repeated identical calls, so 32 of them burned 372k tokens
   before the run failed.
"""

from __future__ import annotations

import pytest

from src.config import reload_settings
from src.sdk.tools import ToolResult


@pytest.fixture(autouse=True)
def _reload_settings_after_env(monkeypatch):
    """Settings are a cached singleton, so env changes need an explicit reload."""
    yield
    from src.config import reload_settings

    reload_settings()


def _result(tool, **kwargs):
    import asyncio

    return asyncio.run(tool.ainvoke({**kwargs, "user_id": "fsfix"}))


def _text(tool, **kwargs) -> str:
    r = _result(tool, **kwargs)
    return str(getattr(r, "content", r))


# --------------------------------------------------------------------------
# 1. allowed_roots
# --------------------------------------------------------------------------


def test_a_configured_root_is_readable(tmp_path, monkeypatch) -> None:
    from src.sdk.tools_core.filesystem import files_list

    project = tmp_path / "project"
    project.mkdir()
    (project / "f.txt").write_text("hi")
    monkeypatch.setenv("FILESYSTEM_ALLOWED_ROOTS", str(project))
    reload_settings()

    out = _text(files_list, path=str(project))
    assert "f.txt" in out


def test_relative_paths_resolve_against_a_configured_root(tmp_path, monkeypatch) -> None:
    from src.sdk.tools_core.filesystem import files_list

    project = tmp_path / "project"
    project.mkdir()
    (project / "f.txt").write_text("hi")
    monkeypatch.setenv("FILESYSTEM_ALLOWED_ROOTS", str(project))
    reload_settings()

    out = _text(files_list, path="f.txt")
    assert "f.txt" in out


def test_a_path_outside_every_root_still_fails_closed(tmp_path, monkeypatch) -> None:
    from src.sdk.tools_core.filesystem import files_read

    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    monkeypatch.setenv("FILESYSTEM_ALLOWED_ROOTS", str(tmp_path / "allowed"))
    reload_settings()

    out = _text(files_read, path=str(outside))
    assert "outside" in out.lower() or "not allowed" in out.lower()


def test_the_error_names_the_way_out(tmp_path, monkeypatch) -> None:
    """A bare refusal costs the agent ~30 wasted calls; say what to do instead."""
    from src.sdk.tools_core.filesystem import files_read

    target = tmp_path / "nope.txt"
    target.write_text("x")
    monkeypatch.setenv("FILESYSTEM_ALLOWED_ROOTS", str(tmp_path / "allowed"))
    reload_settings()

    out = _text(files_read, path=str(target))
    assert "shell_execute" in out, "error must point at the governed alternative"
    assert "allowed" in out.lower()


def test_data_root_is_always_allowed_without_configuration() -> None:
    """Regression: the assistant's own store must stay reachable by default."""
    from src.sdk.tools_core.filesystem import files_list
    from src.storage.paths import get_paths

    root = get_paths("fsfix").workspace_files_dir()
    assert "outside" not in _text(files_list, path=str(root)).lower()


def test_legacy_workspace_root_still_works_for_relative_paths(tmp_path, monkeypatch) -> None:
    """Don't break the existing documented setting while adding the new one."""
    from src.sdk.tools_core.filesystem import files_list

    project = tmp_path / "legacy"
    project.mkdir()
    (project / "g.txt").write_text("hi")
    monkeypatch.setenv("FILESYSTEM_WORKSPACE_ROOT", str(project))
    reload_settings()

    assert "g.txt" in _text(files_list, path="g.txt")


# --------------------------------------------------------------------------
# 2. paths are never ambiguous
# --------------------------------------------------------------------------


def test_listing_reports_the_resolved_absolute_path(tmp_path, monkeypatch) -> None:
    """`path="."` must never be an unqualified 'Empty directory'."""
    from src.sdk.tools_core.filesystem import files_list

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("FILESYSTEM_ALLOWED_ROOTS", str(tmp_path))
    reload_settings()

    out = _text(files_list, path=str(empty))
    assert out.strip() != "Empty directory: ."
    assert str(empty.resolve()) in out


def test_relative_paths_stay_in_the_workspace_not_the_allowed_roots() -> None:
    """Relative is predictable: the workspace. Configured roots are addressed absolutely.

    Fiddling with 'try each allowed root in turn' would make `r.txt` mean
    different files depending on configuration, which is exactly the ambiguity
    that made `path="."` misleading in the first place.
    """

    from src.sdk.tools_core.filesystem import files_read
    from src.storage.paths import get_paths

    ws = get_paths("fsfix").workspace_files_dir()
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "in_workspace.txt").write_text("workspace file")

    assert "workspace file" in _text(files_read, path="in_workspace.txt")


def test_an_allowed_root_is_reached_with_an_absolute_path(tmp_path, monkeypatch) -> None:
    from src.sdk.tools_core.filesystem import files_read

    monkeypatch.setenv("FILESYSTEM_ALLOWED_ROOTS", str(tmp_path))
    reload_settings()
    (tmp_path / "r.txt").write_text("content here")

    assert "content here" in _text(files_read, path=str(tmp_path / "r.txt"))


# --------------------------------------------------------------------------
# 3. repeated-call budget
# --------------------------------------------------------------------------


def test_repeated_identical_calls_are_stopped_with_a_useful_message() -> None:
    """32 identical `files_list` calls must not be allowed to burn the budget."""
    from src.sdk.repetition import RepetitionGuard, RepetitionLimitReached

    guard = RepetitionGuard(limit=3)
    for _ in range(3):
        guard.check("files_list", '{"path": "/tmp"}')
    with pytest.raises(RepetitionLimitReached) as excinfo:
        guard.check("files_list", '{"path": "/tmp"}')
    assert "files_list" in str(excinfo.value)


def test_different_calls_are_not_counted_as_repeats() -> None:
    from src.sdk.repetition import RepetitionGuard

    guard = RepetitionGuard(limit=2)
    guard.check("files_list", '{"path": "/a"}')
    guard.check("files_list", '{"path": "/b"}')
    guard.check("read", '{"path": "/a"}')
    # None of those were the same (tool, args) three times.
    assert guard.check("files_list", '{"path": "/a"}', ) is None


def test_limit_is_configurable() -> None:
    from src.sdk.repetition import RepetitionGuard, RepetitionLimitReached

    guard = RepetitionGuard(limit=5)
    for _ in range(5):
        guard.check("t", "{}")
    with pytest.raises(RepetitionLimitReached):
        guard.check("t", "{}")


# --------------------------------------------------------------------------
# end-to-end: the loop must stop a probing run, not just the module complain
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_loop_stops_a_run_that_keeps_probing_one_tool() -> None:
    """The live failure: 32 files_list calls, 372k tokens, no answer."""
    from src.sdk.loop import AgentLoop
    from src.sdk.messages import Message, ToolCall
    from src.sdk.providers.base import LLMProvider

    budget = int(json_settings("filesystem").max_repeated_tool_calls)
    calls: list[str] = []

    class _P(LLMProvider):
        provider_id = "probe"
        model = "probe"

        async def chat(self, messages, **kw):
            calls.append("chat")
            return Message.assistant(
                content="",
                tool_calls=[
                    ToolCall(id=f"c{len(calls)}", name="files_list",
                             arguments={"path": f"/probe/{len(calls)}"})
                ],
            )

        async def chat_stream(self, messages, **kw):
            raise NotImplementedError

        def count_tokens(self, text, model=None):
            return 4

        def get_model_info(self, model):
            return None

    from src.sdk.tools import ToolAnnotations, ToolDefinition

    def _files_list(path: str = ".", user_id: str = "x", workspace_id: str = "personal"):
        calls.append(f"list:{path}")
        return ToolResult(content="Empty directory: /probe")

    td = ToolDefinition(
        name="files_list",
        description="list",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        annotations=ToolAnnotations(read_only=True),
        function=_files_list,
    )

    from src.sdk.loop import RunConfig

    loop = AgentLoop(provider=_P(), tools=[td], run_config=RunConfig(max_iterations=25))
    await loop.run([Message.user("go")])

    list_calls = [c for c in calls if c.startswith("list:")]
    assert list_calls, "the tool should have run at least once"
    assert len(list_calls) <= budget * 4, (
        f"run was not bounded: {len(list_calls)} calls to one tool"
    )
    assert len(calls) < 25 * 2, "run should have terminated well before the iteration cap"


def json_settings(section: str):
    from src.config import get_settings

    return getattr(get_settings(), section)


@pytest.mark.asyncio
async def test_the_streaming_path_is_bounded_too() -> None:
    """The guard was wired to the non-streaming dispatch only.

    Streaming is the API default, so the budget was effectively off in
    production: 14 shell_execute calls sailed past a budget of 12.
    """
    from src.sdk.loop import AgentLoop, RunConfig
    from src.sdk.messages import Message, StreamChunk
    from src.sdk.providers.base import LLMProvider
    from src.sdk.tools import ToolAnnotations, ToolDefinition

    calls: list[str] = []

    def _sh(command: str = "", user_id: str = "x", workspace_id: str = "personal"):
        calls.append(command)
        return ToolResult(content="total 0")

    td = ToolDefinition(
        name="shell_execute",
        description="run",
        parameters={"type": "object", "properties": {"command": {"type": "string"}}},
        annotations=ToolAnnotations(destructive=True),
        function=_sh,
    )

    def _batch(n: int):
        return [
            StreamChunk.tool_input_start(
                tool="shell_execute", call_id=f"c{n}", args={"command": f"ls /p/{n}"}
            ),
            StreamChunk.tool_input_end(tool="shell_execute", call_id=f"c{n}"),
            StreamChunk.done(content=""),
        ]

    class _P(LLMProvider):
        provider_id = "probe"
        model = "probe"

        def __init__(self):
            self.i = 0

        async def chat(self, messages, **kw):
            return Message.assistant(content="done")

        async def chat_stream(self, messages, **kw):
            n = self.i
            self.i += 1
            for chunk in _batch(n):
                yield chunk

        def count_tokens(self, text, model=None):
            return 4

        def get_model_info(self, model):
            return None

    loop = AgentLoop(provider=_P(), tools=[td], run_config=RunConfig(max_iterations=30))
    _ = [c async for c in loop.run_stream([Message.user("go")])]

    assert calls, "the tool should have run at least once"
    assert len(calls) <= 13, f"streaming run was not bounded: {len(calls)} calls"
