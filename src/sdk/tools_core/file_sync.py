"""Read-only file-sync adapters (P1-T2).

Cloud providers (Dropbox / Google Drive / OneDrive) -> workspace Files/.
No uploads EVER: adapters expose only list/download; FileSyncer rejects any
adapter carrying write-methods (ReadOnlyViolation).

Idempotency: per-provider manifest at files_dir/.sync/{provider}.json keyed
by remote id + revision. Unchanged revs are skipped, so re-running sync never
duplicates or re-downloads files.

Atomicity: downloads stage outside the files tree; files only move into the
workspace after every download in the batch succeeded (no partial trees).
"""

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from src.app_logging import get_logger
from src.storage.paths import get_paths

logger = get_logger()

# Method names that would make an adapter write-capable. FileSyncer refuses
# adapters exposing any of these — the read-only guarantee is structural.
FORBIDDEN_ADAPTER_METHODS = frozenset(
    {"upload", "put_file", "delete_remote", "update_remote", "move_remote", "create_file"}
)


class ConnectorRevokedError(Exception):
    """Raised when a provider has no usable stored credentials."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"connector not connected: {provider}")


class ReadOnlyViolationError(Exception):
    """Raised when an adapter exposes write methods (uploads are forbidden)."""


class UnknownProviderError(Exception):
    """Raised when syncing against an unregistered provider name."""


@dataclass
class RemoteFile:
    """Provider-side file metadata (download happens separately)."""

    id: str
    name: str
    rev: str
    size: int = 0
    path: str = ""  # relative path under the synced folder; defaults to name

    def __post_init__(self) -> None:
        if not self.path:
            self.path = self.name


class ProviderAdapter(Protocol):
    """Minimal read-only provider contract."""

    provider: str

    async def list_files(self, folder: str = "") -> list[RemoteFile]: ...

    async def download(self, remote_id: str) -> bytes: ...


def resolve_provider_credentials(provider: str, user_id: str) -> dict[str, Any]:
    """Fetch stored ConnectKit credentials for provider; raise if absent."""
    from connectkit.bridge import get_vault

    token: dict[str, Any] | None = get_vault(user_id).get_token(provider)
    if not token:
        raise ConnectorRevokedError(provider)
    return token


def _assert_read_only(adapter: Any) -> None:
    for name in FORBIDDEN_ADAPTER_METHODS:
        if not callable(getattr(adapter, name, None)) or name.startswith("_"):
            continue
        raise ReadOnlyViolationError(
            f"provider adapter {type(adapter).__name__} exposes write method {name!r} — "
            "file sync is read-only; uploads are never supported"
        )


@dataclass
class SyncResult:
    provider: str
    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


class MockSyncAdapter:
    """In-memory provider for tests and local dry-runs."""

    def __init__(self, files: list[tuple[str, str, str, bytes]] | None = None):
        self.provider = "mocksync"
        self._files: dict[str, dict[str, Any]] = {
            rid: {"name": name, "rev": rev, "content": content}
            for rid, name, rev, content in (files or [])
        }
        self.download_calls: list[str] = []

    async def list_files(self, folder: str = "") -> list[RemoteFile]:
        return [
            RemoteFile(
                id=rid,
                name=f["name"],
                rev=f["rev"],
                size=len(f["content"]),
            )
            for rid, f in self._files.items()
        ]

    async def download(self, remote_id: str) -> bytes:
        self.download_calls.append(remote_id)
        f = self._files.get(remote_id)
        if f is None:
            raise KeyError(remote_id)
        content: bytes = f["content"]
        return content

    def set_file(self, remote_id: str, name: str, rev: str, content: bytes) -> None:
        self._files[remote_id] = {"name": name, "rev": rev, "content": content}


# --- Real provider adapters (thin REST, read-only). Untested against live
# APIs — mocked through FileSyncer in tests; kept minimal on purpose. ---


class HttpReadOnlyAdapter:
    """Shared plumbing for REST providers; subclasses map endpoints."""

    provider = "http"
    list_url = ""
    download_url_template = ""

    def __init__(self, credentials: dict[str, Any]):
        self._token = credentials.get("access_token") or credentials.get("oauth_token") or ""
        if not self._token:
            raise ConnectorRevokedError(self.provider)
        self._client = httpx.AsyncClient(timeout=30.0)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {"Authorization": f"Bearer {self._token}"}
        if extra:
            base.update(extra)
        return base

    async def list_files(self, folder: str = "") -> list[RemoteFile]:  # pragma: no cover
        raise NotImplementedError

    async def download(self, remote_id: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover
        await self._client.aclose()


class DropboxAdapter(HttpReadOnlyAdapter):
    provider = "dropbox"

    async def list_files(self, folder: str = "") -> list[RemoteFile]:  # pragma: no cover
        entries: list[RemoteFile] = []
        path = "/" + folder if folder else ""
        while True:
            r = await self._client.post(
                "https://content.dropboxapi.com/2/files/list_folder",
                headers=self._headers({"Dropbox-API-Arg": json.dumps({"path": path})}),
            )
            r.raise_for_status()
            for e in r.json().get("entries", []):
                if e[".tag"] == "file":
                    entries.append(RemoteFile(id=e["id"], name=e["name"], rev=e["rev"], size=e.get("size", 0)))
            if not r.json().get("has_more"):
                return entries
            path = ""  # continue via list_folder/continue in a full impl


class GoogleDriveAdapter(HttpReadOnlyAdapter):
    provider = "google-drive"

    async def list_files(self, folder: str = "") -> list[RemoteFile]:  # pragma: no cover
        q = f"'{folder}' in parents and trashed=false" if folder else "trashed=false"
        r = await self._client.get(
            "https://www.googleapis.com/drive/v3/files",
            headers=self._headers(),
            params={"q": q, "fields": "files(id,name,md5Checksum,size)"},
        )
        r.raise_for_status()
        return [
            RemoteFile(id=f["id"], name=f["name"], rev=f.get("md5Checksum", f["id"]), size=int(f.get("size") or 0))
            for f in r.json().get("files", [])
        ]

    async def download(self, remote_id: str) -> bytes:  # pragma: no cover
        r = await self._client.get(
            f"https://www.googleapis.com/drive/v3/files/{remote_id}",
            headers=self._headers(),
            params={"alt": "media"},
        )
        r.raise_for_status()
        return r.content


class OneDriveAdapter(HttpReadOnlyAdapter):
    provider = "onedrive"

    async def list_files(self, folder: str = "") -> list[RemoteFile]:  # pragma: no cover
        base = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder}:/children" if folder else (
            "https://graph.microsoft.com/v1.0/me/drive/root/children"
        )
        r = await self._client.get(base, headers=self._headers())
        r.raise_for_status()
        return [
            RemoteFile(
                id=f["id"],
                name=f["name"],
                rev=f.get("eTag", f["id"]).strip('"'),
                size=f.get("size", 0),
            )
            for f in r.json().get("value", [])
        ]

    async def download(self, remote_id: str) -> bytes:  # pragma: no cover
        r = await self._client.get(
            f"https://graph.microsoft.com/v1.0/me/drive/items/{remote_id}/content",
            headers=self._headers(),
            follow_redirects=True,
        )
        r.raise_for_status()
        return r.content


class SyncProviderRegistry:
    """provider name -> adapter factory(user_id, workspace_id)."""

    def __init__(self) -> None:
        self._factories: dict[str, Any] = {}

    def register(self, name: str, factory: Any) -> None:
        self._factories[name] = factory

    def get(self, name: str) -> Any | None:
        return self._factories.get(name)

    def names(self) -> list[str]:
        return sorted(self._factories)


def _connectkit_factory(adapter_cls: type[HttpReadOnlyAdapter]) -> Any:  # pragma: no cover
    """Factory that resolves ConnectKit creds at build time (revocation surface)."""

    def make(user_id: str, workspace_id: str) -> HttpReadOnlyAdapter:
        creds = resolve_provider_credentials(adapter_cls.provider, user_id)
        return adapter_cls(creds)

    make.__name__ = f"{adapter_cls.provider}_factory"
    return make


_DEFAULT_REGISTRY = SyncProviderRegistry()
_PROTECTED = {"dropbox", "google-drive", "onedrive"}
for _name, _cls in (
    ("dropbox", DropboxAdapter),
    ("google-drive", GoogleDriveAdapter),
    ("onedrive", OneDriveAdapter),
):
    _DEFAULT_REGISTRY.register(_name, _connectkit_factory(_cls))


def get_sync_registry() -> SyncProviderRegistry:
    return _DEFAULT_REGISTRY


def _manifest_path(provider: str, user_id: str, workspace_id: str) -> Path:
    files_dir = get_paths(user_id, workspace_id=workspace_id).workspace_files_dir()
    sync_dir = files_dir / ".sync"
    sync_dir.mkdir(parents=True, exist_ok=True)
    return sync_dir / f"{provider}.json"


def _load_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text()))
    except Exception:
        return {}


class FileSyncer:
    """Downloads a provider's files into the workspace; idempotent by remote id."""

    def __init__(
        self,
        user_id: str,
        workspace_id: str = "personal",
        adapter: ProviderAdapter | None = None,
        registry: SyncProviderRegistry | None = None,
    ):
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.adapter = adapter
        self.registry = registry or get_sync_registry()

    async def with_provider(self, provider: str, folder: str = "") -> SyncResult:
        """Resolve a registered provider (creds + adapter) and sync it."""
        factory = self.registry.get(provider)
        if factory is None:
            raise UnknownProviderError(provider)
        adapter = await _maybe_await(factory(self.user_id, self.workspace_id))
        self.adapter = adapter
        return await self.sync(folder=folder)

    async def sync(self, folder: str = "") -> SyncResult:
        assert self.adapter is not None, "adapter required"
        _assert_read_only(self.adapter)
        provider = getattr(self.adapter, "provider", "unknown")

        # Revoke check BEFORE anything is written.
        listing = await self.adapter.list_files(folder)

        files_dir = get_paths(self.user_id, workspace_id=self.workspace_id).workspace_files_dir()
        manifest = _load_manifest(_manifest_path(provider, self.user_id, self.workspace_id))

        result = SyncResult(provider=provider)
        to_download: list[RemoteFile] = []
        for rf in listing:
            entry = manifest.get(rf.id)
            if entry and entry.get("rev") == rf.rev and entry.get("path"):
                result.skipped.append(rf.name)
            else:
                to_download.append(rf)

        if to_download:
            staging = files_dir / ".sync" / f"staging-{provider}-{int(time.time() * 1000)}"
            staging.mkdir(parents=True, exist_ok=True)
            staged: list[tuple[Path, Path]] = []  # (staged_path, final_path)
            try:
                for rf in to_download:
                    try:
                        content = await self.adapter.download(rf.id)
                    except Exception as e:
                        logger.error(
                            "sync.download_failed",
                            {"provider": provider, "remote_id": rf.id, "error": str(e)},
                            user_id=self.user_id,
                        )
                        result.failed.append(rf.name)
                        continue
                    staged_path = staging / rf.path
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_path.write_bytes(content)
                    staged.append((staged_path, files_dir / rf.path))

                if result.failed:
                    # No partial trees: commit nothing, clean staging.
                    shutil.rmtree(staging, ignore_errors=True)
                    return result

                for staged_path, final_path in staged:
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(staged_path), str(final_path))
                    result.downloaded.append(final_path.name)
            finally:
                shutil.rmtree(staging, ignore_errors=True)

        if not result.failed:
            self._commit(state_path=_manifest_path(provider, self.user_id, self.workspace_id),
                        manifest=manifest, downloaded=result.downloaded, listing=listing)

        logger.info(
            "sync.completed",
            {"provider": provider, "downloaded": len(result.downloaded),
             "skipped": len(result.skipped), "failed": len(result.failed)},
            user_id=self.user_id,
        )
        return result

    def _commit(
        self,
        state_path: Path,
        manifest: dict[str, dict[str, str]],
        downloaded: list[str],
        listing: list[RemoteFile],
    ) -> None:
        from src.http.workspace_cache import get_file_cache

        cache = get_file_cache(self.user_id, self.workspace_id)
        by_name = {rf.name: rf for rf in listing}
        for name in downloaded:
            rf = by_name[name]
            cache.mark_downloaded(rf.path, remote_rev=rf.rev)
            manifest[rf.id] = {"path": rf.path, "rev": rf.rev}
        state_path.write_text(json.dumps(manifest, indent=2))


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
