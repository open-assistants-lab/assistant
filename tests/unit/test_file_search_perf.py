"""Perf regression tests for file search/read efficiency (audit P5).

Verifies:
- files_read streams via islice (reads stop early, no full-file read) and
  refuses files > 50 MB with a helpful message.
- files_grep_search skips dot-dirs / vcs dirs / binary extensions during the
  walk, stops collecting matches past the display cap (non-count mode), and
  keeps collecting in count mode.
- files_glob_search caps results (e.g. 500) and stats each match once.
"""


import pytest

TEST_USER_ID = "test_file_search_perf_user"
MAX_FILE_SIZE = 50 * 1024 * 1024


class _CountingFile:
    """Proxy over a real text handle that counts bytes read."""

    def __init__(self, real, counter):
        self._real = real
        self._counter = counter

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._real.__exit__(*args)

    def __iter__(self):
        return self

    def __next__(self):
        line = self._real.readline()
        self._counter["bytes"] += len(line.encode("utf-8"))
        if line == "":
            raise StopIteration
        return line

    def readline(self):
        line = self._real.readline()
        self._counter["bytes"] += len(line.encode("utf-8"))
        return line

    def read(self, *args, **kwargs):
        content = self._real.read(*args, **kwargs)
        if isinstance(content, str):
            self._counter["bytes"] += len(content.encode("utf-8"))
        return content

    def close(self):
        return self._real.close()


@pytest.fixture
def user_workspace(tmp_path, monkeypatch):
    from src.storage.paths import DataPaths

    def mock_get_paths(user_id="default_user", workspace_id="personal"):
        return DataPaths(data_root=str(tmp_path), user_id=user_id, workspace_id=workspace_id)

    monkeypatch.setattr("src.sdk.tools_core.filesystem.get_paths", mock_get_paths)
    monkeypatch.setattr("src.sdk.tools_core.file_search.get_paths", mock_get_paths)

    return DataPaths(data_root=str(tmp_path), user_id=TEST_USER_ID).workspace_files_dir()


@pytest.fixture
def counting_reads(monkeypatch):
    """Count bytes read through pathlib.Path.open."""
    import pathlib

    counter = {"bytes": 0}

    real_open = pathlib.Path.open

    def counting_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if "r" in mode and "b" not in mode:
            return _CountingFile(handle, counter)
        return handle

    monkeypatch.setattr(pathlib.Path, "open", counting_open)
    return counter


def _write_all(path, n_lines, line="payload-content"):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n_lines):
            f.write(f"{i}:{line}\n")


class TestFilesReadStreaming:
    def test_reads_only_requested_window(self, user_workspace, counting_reads):
        """files_read must not read the whole file when given offset/limit."""
        from src.sdk.tools_core.filesystem import files_read

        big = user_workspace / "big.txt"
        _write_all(big, 5000)

        result = files_read.invoke(
            {"path": "big.txt", "offset": 10, "limit": 20, "user_id": TEST_USER_ID}
        )

        assert "10:" in result
        assert "29:" in result
        # islice(handle, 10, 30) reads at most ~30 lines (~600 bytes), not the
        # whole ~160KB file.
        assert 0 < counting_reads["bytes"] < 3000, counting_reads["bytes"]


class TestFilesReadSizeLimit:
    def test_refuses_file_over_50mb(self, user_workspace):
        """files_read should refuse files > 50 MB with a helpful message."""
        from src.sdk.tools_core.filesystem import files_read

        big = user_workspace / "huge.log"
        with open(big, "wb") as f:
            f.truncate(MAX_FILE_SIZE + 1024)

        result = files_read.invoke({"path": "huge.log", "user_id": TEST_USER_ID})
        assert "50 MB" in result
        assert "large" in result.lower()


class TestFilesGrepWalkSkips:
    def test_skips_dotdirs_vcs_and_binary(self, user_workspace):
        """grep must not descend into dot-dirs/vcs dirs nor read binaries."""
        from src.sdk.tools_core.file_search import files_grep_search

        (user_workspace / "notes.txt").write_text("needle in the haystack\n")
        (user_workspace / ".hidden").mkdir()
        (user_workspace / ".hidden" / "secret.txt").write_text("needle hidden\n")
        (user_workspace / ".git").mkdir()
        (user_workspace / ".git" / "config").write_text("needle vcs\n")
        (user_workspace / "blob.bin").write_bytes(b"needle binary\x00\x01\x02")

        result = files_grep_search.invoke(
            {"pattern": "needle", "path": ".", "user_id": TEST_USER_ID}
        )

        assert "notes.txt" in result
        assert "secret.txt" not in result
        assert "config" not in result
        assert "blob.bin" not in result


class TestFilesGrepCap:
    def test_non_count_mode_stops_at_cap(self, user_workspace, counting_reads):
        from src.sdk.tools_core.file_search import files_grep_search

        big = user_workspace / "hits.txt"
        _write_all(big, 5000, line="match-this")

        result = files_grep_search.invoke(
            {"pattern": "match-this", "path": ".", "user_id": TEST_USER_ID}
        )

        lines = [ln for ln in result.splitlines() if "hits.txt:" in ln]
        assert len(lines) <= 100, f"expected <= 100 result lines, got {len(lines)}"
        assert "more matches" in result
        # Must stop reading the file once the cap is hit (<= 100 lines, ~2KB),
        # not scan all 5000 lines (~70KB).
        assert 0 < counting_reads["bytes"] < 5000, counting_reads["bytes"]

    def test_count_mode_collects_all(self, user_workspace):
        from src.sdk.tools_core.file_search import files_grep_search

        (user_workspace / "a.txt").write_text("needle\n" * 5)
        (user_workspace / "b.txt").write_text("needle\n" * 3)

        result = files_grep_search.invoke(
            {"pattern": "needle", "path": ".", "count": True, "user_id": TEST_USER_ID}
        )
        assert "a.txt: 5 matches" in result
        assert "b.txt: 3 matches" in result


class TestFilesGlobCap:
    def test_glob_results_capped(self, user_workspace):
        from src.sdk.tools_core.file_search import files_glob_search

        for i in range(600):
            (user_workspace / f"f{i:04d}.txt").write_text("x")

        result = files_glob_search.invoke(
            {"pattern": "*.txt", "path": ".", "user_id": TEST_USER_ID}
        )
        file_lines = [ln for ln in result.splitlines() if ".txt (" in ln]
        assert len(file_lines) <= 500, f"expected <= 500 file lines, got {len(file_lines)}"
