"""Unit tests for the file-sync adapter (P1-T2).

Read-only cloud sync: provider Files -> workspace Files/. No uploads EVER.
Provider adapters are tested via a mock; no network calls.
"""

from dataclasses import dataclass

import pytest

from src.sdk.tools_core.file_sync import (
    ConnectorRevokedError,
    FileSyncer,
    ReadOnlyViolationError,
    RemoteFile,
)
from src.storage.paths import get_paths


@dataclass
class MockFile:
    remote_id: str
    name: str
    remote_rev: str
    content: bytes


class MockProvider:
    """In-memory read-only provider for tests."""

    provider = "mock"

    def __init__(self, files: list[MockFile]):
        self._files = {f.remote_id: f for f in files}
        self.downloads: list[str] = []

    async def list_files(self, folder: str = "") -> list[RemoteFile]:
        return [
            RemoteFile(id=f.remote_id, name=f.name, rev=f.remote_rev, size=len(f.content))
            for f in self._files.values()
        ]

    async def download(self, remote_id: str) -> bytes:
        self.downloads.append(remote_id)
        return self._files[remote_id].content


class WriteCapableProvider(MockProvider):
    """A provider exposing a write method — must be rejected."""

    def upload(self, name: str, content: bytes) -> None:  # pragma: no cover
        pass

    def delete_remote(self, remote_id: str) -> None:  # pragma: no cover
        pass


@pytest.fixture
def syncer_factory(user_env):
    """FileSyncer factory rooted at a tmp data dir per test."""

    def make(adapter, user_id="sync_user", workspace_id="personal"):
        return FileSyncer(
            user_id=user_id, workspace_id=workspace_id, adapter=adapter
        )

    return make


@pytest.fixture
def user_env(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(paths_mod.DataPaths, "root", property(lambda self: tmp_path / "data"))
    # Clear stale get_paths cache entries between tests.
    paths_mod._paths_cache.clear()
    yield tmp_path
    paths_mod._paths_cache.clear()


def _three_files():
    return [
        MockFile("r1", "plan.md", "rev1", b"# Plan"),
        MockFile("r2", "budget.csv", "rev2", b"a,b\n1,2"),
        MockFile("r3", "notes.txt", "rev3", b"hello"),
    ]


@pytest.mark.asyncio
async def test_sync_downloads_all_files_as_cloud_only_to_downloaded(syncer_factory):
    files = _three_files()
    adapter = MockProvider(files)
    result = await syncer_factory(adapter).sync()
    assert result.provider == "mock"
    assert set(result.downloaded) == {"plan.md", "budget.csv", "notes.txt"}
    assert result.failed == []

    from src.http.workspace_cache import get_file_cache

    cache = get_file_cache("sync_user")
    for name in ("plan.md", "budget.csv", "notes.txt"):
        assert cache.get_status(name) == "downloaded"


@pytest.mark.asyncio
async def test_sync_rerun_idempotent_by_remote_id(syncer_factory):
    files = _three_files()
    adapter = MockProvider(files)
    fs = syncer_factory(adapter)

    first = await fs.sync()
    assert len(first.downloaded) == 3

    second = await fs.sync()
    # No re-download: all files skipped by remote id + rev.
    assert second.downloaded == []
    assert set(second.skipped) == {"plan.md", "budget.csv", "notes.txt"}
    assert adapter.downloads == ["r1", "r2", "r3"]  # download count unchanged


@pytest.mark.asyncio
async def test_sync_changed_rev_redownloads(syncer_factory):
    files = _three_files()
    adapter = MockProvider(files)
    fs = syncer_factory(adapter)
    await fs.sync()

    files[0].remote_rev = "rev1b"
    files[0].content = b"# Plan v2"
    third = await fs.sync()
    assert third.downloaded == ["plan.md"]
    assert "r1" in adapter.downloads


@pytest.mark.asyncio
async def test_sync_no_partial_tree_on_mid_download_failure(syncer_factory):
    files = _three_files()

    class FailingAtSecond(MockProvider):
        async def download(self, remote_id: str) -> bytes:
            if remote_id == "r2":
                raise ConnectionError("provider reset connection")
            return await super().download(remote_id)

    adapter = FailingAtSecond(files)
    result = await syncer_factory(adapter).sync()

    assert result.failed == ["budget.csv"]
    assert result.downloaded == []  # NOTHING committed

    from src.http.workspace_cache import get_file_cache

    cache = get_file_cache("sync_user")
    assert cache.get_status("plan.md") == "cloud_only"  # nothing marked downloaded

    from src.storage.paths import get_paths

    root = get_paths("sync_user").workspace_files_dir()
    assert not (root / "plan.md").exists()
    assert not (root / "budget.csv").exists()
    # No staging dirs leaked
    assert not list(root.glob(".sync/*staging*"))


@pytest.mark.asyncio
async def test_sync_rejects_write_capable_adapter(syncer_factory):
    with pytest.raises(ReadOnlyViolationError):
        await syncer_factory(WriteCapableProvider(_three_files())).sync()


@pytest.mark.asyncio
async def test_revoked_connector_raises(syncer_factory, monkeypatch):
    """Adapter with no stored credentials raises ConnectorRevokedError."""

    def fake_vault(user_id):
        class NoVault:
            def get_token(self, service):
                return None

        return NoVault()

    monkeypatch.setattr(
        "src.sdk.tools_core.file_sync.resolve_provider_credentials",
        lambda provider, user_id: fake_vault(user_id).get_token(provider)
        or (_ for _ in ()).throw(ConnectorRevokedError(provider)),
    )
    with pytest.raises(ConnectorRevokedError):
        await syncer_factory(
            MockProvider(_three_files()),
        ).with_provider("dropbox")


@pytest.mark.asyncio
async def test_sync_100_files_under_60s(syncer_factory):
    files = [
        MockFile(f"r{i}", f"doc_{i}.txt", f"rev{i}", b"x" * 128) for i in range(100)
    ]
    import time

    start = time.monotonic()
    result = await syncer_factory(MockProvider(files)).sync()
    elapsed = time.monotonic() - start

    assert len(result.downloaded) == 100
    assert elapsed < 60.0


async def _duplicate_listing(folder: str = "") -> list[RemoteFile]:
    """Two remotes, same basename, different folder paths."""
    return [
        RemoteFile(id="id-a", name="report.pdf", rev="r1", path="clients/a/report.pdf"),
        RemoteFile(id="id-b", name="report.pdf", rev="r2", path="clients/b/report.pdf"),
    ]


@pytest.mark.asyncio
async def test_sync_duplicate_basenames_idempotent(syncer_factory):
    """Duplicate basenames in different folders each keep their own manifest
    entry keyed on remote id — rerun downloads nothing (review P1 finding)."""
    a = MockFile("id-a", "report.pdf", "r1", b"A")
    b = MockFile("id-b", "report.pdf", "r2", b"B")
    syncer = syncer_factory(MockProvider([a, b]))
    # patch provider listing to use folder-qualified paths
    syncer.adapter.list_files = _duplicate_listing  # type: ignore[method-assign]
    r1 = await syncer.sync()
    assert sorted(r1.downloaded) == ["report.pdf", "report.pdf"]
    files_dir = get_paths(syncer.user_id, workspace_id=syncer.workspace_id).workspace_files_dir()
    assert (files_dir / "clients/a/report.pdf").read_bytes() == b"A"
    assert (files_dir / "clients/b/report.pdf").read_bytes() == b"B"

    r2 = await syncer.sync()
    assert r2.downloaded == []  # nothing redownloaded
    assert len(r2.skipped) == 2
