"""B7: files_* text fidelity — edits, newlines, versions, symlinks, args (#85-#90)."""


import pytest

from src.sdk.tools_core import file_search as search_mod
from src.sdk.tools_core import file_versioning as versioning
from src.sdk.tools_core import filesystem as fs


@pytest.fixture
def files_env(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import src.config as cfg
    from src.storage.paths import DataPaths

    root = tmp_path / "store"
    settings = SimpleNamespace(filesystem=SimpleNamespace(allowed_roots=[], workspace_root=None))
    monkeypatch.setattr(cfg, "get_settings", lambda: settings)
    monkeypatch.setattr(
        fs, "get_paths",
        lambda user, workspace_id="personal": DataPaths(
            user_id=user, data_root=root, data_path=tmp_path / "cfg", workspace_id=workspace_id
        ),
    )
    monkeypatch.setattr(
        versioning, "get_paths",
        lambda user, workspace_id="personal": DataPaths(
            user_id=user, data_root=root, data_path=tmp_path / "cfg", workspace_id=workspace_id
        ),
    )
    monkeypatch.setattr(
        search_mod, "get_paths",
        lambda user, workspace_id="personal": DataPaths(
            user_id=user, data_root=root, data_path=tmp_path / "cfg", workspace_id=workspace_id
        ),
    )
    ws = fs.get_paths("u").workspace_files_dir()
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(search_mod, "Path", search_mod.Path)  # real Path
    return root, ws, settings


class TestEmptyOldRefused:
    """#85: old="" inserts new between every character of the file."""

    @pytest.mark.asyncio
    async def test_edit_with_empty_old_is_refused(self, files_env):
        root, ws, _ = files_env
        f = ws / "note.txt"
        f.write_text("abc")
        result = await fs.files_edit.ainvoke({"path": "note.txt", "old": "", "new": "X", "user_id": "u"})
        text = str(getattr(result, "content", result))
        assert "old" in text.lower() and ("empty" in text.lower() or "required" in text.lower()), text
        assert f.read_text() == "abc", "the file was corrupted by an empty-old edit"


class TestCrlfPreserved:
    """#86: editing one line of a CRLF file must not rewrite every line ending."""

    @pytest.mark.asyncio
    async def test_crlf_file_stays_crlf(self, files_env):
        root, ws, _ = files_env
        f = ws / "win.txt"
        f.write_bytes(b"line1\r\nline2\r\nline3\r\n")
        result = await fs.files_edit.ainvoke(
            {"path": "win.txt", "old": "line2", "new": "line2-edited", "user_id": "u"}
        )
        assert "error" not in str(getattr(result, "content", result)).lower() or "success" in str(getattr(result, "content", result)).lower()
        data = f.read_bytes()
        assert data.count(b"\r\n") == 3, f"CRLF line endings rewritten: {data!r}"


class TestReadDoesNotDoubleNewlines:
    """#87: files_read must not insert a blank line between every line."""

    @pytest.mark.asyncio
    async def test_read_preserves_line_structure(self, files_env):
        root, ws, _ = files_env
        f = ws / "plain.txt"
        f.write_bytes(b"l1\nl2\nl3\n")
        result = await fs.files_read.ainvoke({"path": "plain.txt", "user_id": "u"})
        text = str(getattr(result, "content", result))
        assert "l1\nl2\nl3" in text or "l1\n l2\n l3" not in text, text
        assert "\n\n" not in text.split("l1")[-1] or "l1\n\nl2" not in text, (
            f"blank lines inserted between every line: {text!r}"
        )


class TestSameSecondVersions:
    """#88: two edits within the same second must not overwrite each other."""

    @pytest.mark.asyncio
    async def test_rapid_edits_keep_each_version(self, files_env, monkeypatch):
        root, ws, _ = files_env
        f = ws / "v.txt"
        f.write_text("v0")

        # Same wall second, different microseconds — exactly the burst that
        # used to clobber one 1-second version file.
        stamps = iter([
            "2026-10-04T10:00:00.000001",
            "2026-10-04T10:00:00.000002",
            "2026-10-04T10:00:00.000003",
            "2026-10-04T10:00:00.000004",
        ])

        class _FakeDT(versioning.datetime):
            @classmethod
            def now(cls, tz=None):
                return versioning.datetime.fromisoformat(next(stamps)).replace(tzinfo=tz)

        monkeypatch.setattr(versioning, "datetime", _FakeDT)

        for a, b in [("v0", "v1"), ("v1", "v2"), ("v2", "v3")]:
            await fs.files_edit.ainvoke({"path": "v.txt", "old": a, "new": b, "user_id": "u"})

        ver_dir = versioning._version_path("u", "v.txt")
        names = sorted(p.name for p in ver_dir.iterdir()) if ver_dir.exists() else []
        assert len({n for n in names}) >= 3, f"rapid edits overwrote versions: {names}"
        contents = [p.read_text() for p in sorted(ver_dir.iterdir())]
        assert "v0" in contents and "v1" in contents and "v2" in contents, contents
        assert "v0" in contents and "v1" in contents, contents


class TestSymlinkedWorkspaceSearch:
    """#89: rel paths must be computed against the RESOLVED root."""

    @pytest.mark.asyncio
    async def test_glob_and_grep_work_through_a_symlinked_workspace(self, files_env):
        root, ws, _ = files_env
        real_dir = root / "real-files"
        real_dir.mkdir()
        (real_dir / "needle.txt").write_text("findme here\n")
        ws_dir = fs.get_paths("u").workspace_files_dir()
        (real_dir.parent / ws_dir.name).rmdir() if False else None
        # Replace the workspace dir with a symlink
        import shutil
        shutil.rmtree(ws_dir)
        ws_dir.symlink_to(real_dir)

        glob_result = await search_mod.files_glob_search.ainvoke({"pattern": "**/*.txt", "path": ".", "user_id": "u"})
        glob_text = str(getattr(glob_result, "content", glob_result))
        assert "needle.txt" in glob_text, f"glob failed through a symlinked workspace: {glob_text[:160]}"

        grep_result = await search_mod.files_grep_search.ainvoke({"pattern": "findme", "path": ".", "user_id": "u"})
        grep_text = str(getattr(grep_result, "content", grep_result))
        assert "needle.txt" in grep_text, f"grep silently missed: {grep_text[:160]}"


class TestNonDictArgsDoNotAbort:
    """#90: arguments that parse to a non-object abort the run."""

    @pytest.mark.asyncio
    async def test_loop_survives_a_null_argument_stream(self):
        from src.sdk.loop import AgentLoop
        from src.sdk.messages import Message, StreamChunk
        from tests.sdk.test_sdk_loop import MockProvider

        provider = MockProvider()
        provider.set_stream_events([
            [
                StreamChunk.tool_input_start(tool="reader", call_id="a"),
                StreamChunk.tool_input_delta(call_id="a", content="null"),
                StreamChunk.tool_input_end(tool="reader", call_id="a"),
                StreamChunk.done(content=""),
            ],
            [StreamChunk.text_delta(content="recovered"), StreamChunk.done(content="recovered")],
        ])
        loop = AgentLoop(provider=provider, tools=[fs.files_read])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        assert any(c.canonical_type == "done" for c in chunks), "the run aborted on non-object args"
        errors = [c for c in chunks if c.canonical_type == "error"]
        assert errors, "the malformed arguments passed silently"
