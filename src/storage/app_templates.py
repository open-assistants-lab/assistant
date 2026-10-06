"""Optional operator-installed app templates; never migrates legacy user stores.

Run on an initial/stopped store, not concurrently with an application writer.
User-owned apps, modified schemas/markers and populated tables are not refreshed.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from src.sdk import HybridDB
from src.sdk.tools_core.apps import EMBEDDING_MODEL
from src.storage.paths import DEFAULT_USER_ID, DataPaths


@dataclass(frozen=True)
class AppTemplate:
    name: str
    description: str
    tables: dict[str, dict[str, str]]


def _encoded(template: AppTemplate) -> str:
    return json.dumps(asdict(template), sort_keys=True, separators=(",", ":"))


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_app_templates(seed_dir: Path | None = None) -> list[AppTemplate]:
    directory = seed_dir or Path(__file__).resolve().parents[2] / "seeds" / "apps"
    if not directory.is_dir():
        raise FileNotFoundError(f"App template directory is missing: {directory}")
    templates = []
    seen = set()
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        template = AppTemplate(**data)
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", template.name):
            raise ValueError(f"Invalid app template name: {template.name!r}")
        safe_name = template.name.replace("-", "_")
        if safe_name in seen:
            raise ValueError(f"Duplicate app template name: {template.name}")
        seen.add(safe_name)
        if not isinstance(template.description, str) or not template.tables:
            raise ValueError(f"Invalid app template: {path.name}")
        for table, columns in template.tables.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", table) or not columns:
                raise ValueError(f"Invalid template table: {table!r}")
            for column, kind in columns.items():
                if not re.fullmatch(r"[a-z][a-z0-9_]*", column) or column == "id":
                    raise ValueError(f"Invalid template column: {column!r}")
                if kind not in {"TEXT", "INTEGER", "REAL", "BOOLEAN"}:
                    raise ValueError(f"Unsupported template type: {kind!r}")
        templates.append(template)
    return templates


def _write_definition(path: Path, content: str) -> None:
    (path / ".seed-template.json").write_text(content, encoding="utf-8")
    (path / ".seed-hash").write_text(_digest(content), encoding="utf-8")


def _create_tables(db: HybridDB, template: AppTemplate) -> None:
    for table, columns in template.tables.items():
        db.create_table(table, columns)


def seed_app_templates(
    user_id: str, *, data_root: Path, seed_dir: Path | None = None,
) -> list[str]:
    """Return installed/already-current template names, skipping user-owned state.

    Refresh only unchanged, empty, seed-owned schemas with additive changes.
    Changed definitions cannot automatically remove columns/tables or change types.
    No startup hook or automatic export/import of retired stores is installed.
    """
    templates = load_app_templates(seed_dir)
    boundary = data_root.expanduser().resolve()
    paths = DataPaths(user_id=user_id, data_root=str(boundary), data_path=str(boundary))
    # user_dir creates directories: derive its canonical layout before touching it.
    user_root = boundary if paths.user_id == DEFAULT_USER_ID else boundary / "Users" / paths.user_id
    root = user_root / "Apps"
    for component in (root, *root.parents):
        if component == boundary:
            break
        if component.is_symlink():
            raise ValueError("Refuse symlink inside app template data root")
    root.mkdir(parents=True, exist_ok=True)
    installed = []
    for template in templates:
        path = root / template.name.replace("-", "_")
        content = _encoded(template)
        if path.is_symlink():
            raise ValueError(f"Refuse symlink app template destination: {template.name}")
        if not path.exists():
            with tempfile.TemporaryDirectory(prefix=".seed-", dir=root) as temporary:
                staged = Path(temporary)
                db = HybridDB(str(staged), embedding_model_name=EMBEDDING_MODEL)
                try:
                    _create_tables(db, template)
                finally:
                    db.close()
                _write_definition(staged, content)
                staged.rename(path)
            installed.append(template.name)
            continue
        marker = path / ".seed-hash"
        snapshot = path / ".seed-template.json"
        for name in (".seed-hash", ".seed-template.json", "app.db", "app.db-wal", "app.db-shm"):
            if (path / name).is_symlink():
                raise ValueError(f"Refuse symlink app template state: {template.name}")
        if not marker.is_file() or not snapshot.is_file() or not (path / "app.db").is_file():
            continue
        # HybridDB initializes SQLite, vector and optional analytics state on open.
        # Reject every nested link before allowing a writable backend constructor.
        if any(component.is_symlink() for component in path.rglob("*")):
            raise ValueError(f"Refuse symlink app template state: {template.name}")
        old_content = snapshot.read_text(encoding="utf-8")
        if marker.read_text(encoding="utf-8") != _digest(old_content):
            continue
        old = AppTemplate(**json.loads(old_content))
        db = HybridDB(str(path), embedding_model_name=EMBEDDING_MODEL)
        try:
            actual = {table: db.get_schema(table) for table in db.list_tables()}
            if actual != old.tables:
                continue
            if old_content == content:
                installed.append(template.name)
                continue
            if any(db.count(table) for table in old.tables):
                continue
            if any(
                table not in template.tables
                or any(template.tables[table].get(column) != kind for column, kind in columns.items())
                for table, columns in old.tables.items()
            ):
                continue
            _create_tables(db, template)
            _write_definition(path, content)
            installed.append(template.name)
        finally:
            db.close()
    return installed
