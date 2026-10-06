"""Optional template installation creates real apps without overwriting user state."""
import json


def _seed_dir(tmp_path, *, extra=False):
    path = tmp_path / "seeds"
    path.mkdir(exist_ok=True)
    columns = {"title": "TEXT", "status": "TEXT", "due": "TEXT"}
    if extra:
        columns["priority"] = "INTEGER"
    (path / "tasks.json").write_text(json.dumps({
        "name": "tasks", "description": "Fixture tasks", "tables": {"items": columns},
    }))
    return path


def test_templates_load_from_seed_dir():
    from src.storage.app_templates import load_app_templates
    assert {template.name for template in load_app_templates()} == {"tasks", "contacts", "reading-list"}


def test_seeding_materialises_apps_and_tool_can_insert(tmp_path, monkeypatch):
    from src.storage.app_templates import seed_app_templates
    from src.sdk.tools_core import apps
    names = seed_app_templates("u1", data_root=tmp_path)
    assert set(names) == {"tasks", "contacts", "reading-list"}
    app_root = tmp_path / "Users" / "u1" / "Apps"
    assert (app_root / "tasks" / "app.db").exists()
    assert (app_root / "reading_list" / "app.db").exists()
    monkeypatch.setattr(apps, "_get_base_path", lambda _: app_root)
    monkeypatch.setattr(apps, "_dbs", {})
    try:
        inserted = apps.app_insert.invoke({"app": "tasks", "table": "items", "data": {"title": "known task"}, "user_id": "u1"})
        assert not getattr(inserted, "is_error", False), str(inserted)
        queried = apps.app_query.invoke({"app": "tasks", "query": "SELECT title FROM items", "user_id": "u1"})
        assert "known task" in str(queried)
        assert seed_app_templates("u1", data_root=tmp_path) == names
    finally:
        for db in apps._dbs.values():
            db.close()


def test_unmarked_existing_app_is_not_claimed_or_changed(tmp_path):
    from src.storage.app_templates import seed_app_templates
    target = tmp_path / "Users" / "u1" / "Apps" / "tasks"
    target.mkdir(parents=True)
    sentinel = target / "operator-owned"
    sentinel.write_bytes(b"leave-me-alone")
    assert "tasks" not in seed_app_templates("u1", data_root=tmp_path)
    assert list(target.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"leave-me-alone"


def test_untouched_empty_template_refreshes_additive_schema(tmp_path):
    from src.storage.app_templates import seed_app_templates
    from src.sdk import HybridDB
    seed_dir = _seed_dir(tmp_path)
    root = tmp_path / "data"
    seed_app_templates("u1", data_root=root, seed_dir=seed_dir)
    seed_dir = _seed_dir(tmp_path, extra=True)
    assert seed_app_templates("u1", data_root=root, seed_dir=seed_dir) == ["tasks"]
    db = HybridDB(str(root / "Users" / "u1" / "Apps" / "tasks"), embedding_model_name="all-MiniLM-L6-v2")
    try:
        assert db.get_schema("items")["priority"] == "INTEGER"
    finally:
        db.close()


def test_user_rows_survive_changed_seed_without_refresh(tmp_path):
    from src.storage.app_templates import seed_app_templates
    from src.sdk import HybridDB
    seed_dir = _seed_dir(tmp_path)
    root = tmp_path / "data"
    seed_app_templates("u1", data_root=root, seed_dir=seed_dir)
    app = root / "Users" / "u1" / "Apps" / "tasks"
    db = HybridDB(str(app), embedding_model_name="all-MiniLM-L6-v2")
    try:
        db.insert("items", {"title": "user-owned"})
    finally:
        db.close()
    old_hash = (app / ".seed-hash").read_text()
    _seed_dir(tmp_path, extra=True)
    assert seed_app_templates("u1", data_root=root, seed_dir=seed_dir) == []
    assert (app / ".seed-hash").read_text() == old_hash
    db = HybridDB(str(app), embedding_model_name="all-MiniLM-L6-v2")
    try:
        assert db.query("items")[0]["title"] == "user-owned"
        assert "priority" not in db.get_schema("items")
    finally:
        db.close()


def test_modified_seed_marker_is_preserved(tmp_path):
    from src.storage.app_templates import seed_app_templates
    seed_app_templates("u1", data_root=tmp_path)
    marker = tmp_path / "Users" / "u1" / "Apps" / "tasks" / ".seed-hash"
    marker.write_text("user-edited")
    assert "tasks" not in seed_app_templates("u1", data_root=tmp_path)
    assert marker.read_text() == "user-edited"


def test_symlink_app_root_is_refused_without_writing_target(tmp_path):
    import pytest
    from src.storage.app_templates import seed_app_templates
    outside = tmp_path / "other-instance"
    outside.mkdir()
    user_root = tmp_path / "data" / "Users" / "u1"
    user_root.mkdir(parents=True)
    (user_root / "Apps").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        seed_app_templates("u1", data_root=tmp_path / "data")
    assert list(outside.iterdir()) == []


def test_symlink_seed_snapshot_cannot_overwrite_external_file(tmp_path):
    import pytest
    from src.storage.app_templates import seed_app_templates
    seed_dir = _seed_dir(tmp_path)
    root = tmp_path / "data"
    seed_app_templates("u1", data_root=root, seed_dir=seed_dir)
    snapshot = root / "Users" / "u1" / "Apps" / "tasks" / ".seed-template.json"
    outside = tmp_path / "external-snapshot.json"
    content = snapshot.read_bytes()
    outside.write_bytes(content)
    snapshot.unlink()
    snapshot.symlink_to(outside)
    _seed_dir(tmp_path, extra=True)
    with pytest.raises(ValueError, match="symlink"):
        seed_app_templates("u1", data_root=root, seed_dir=seed_dir)
    assert outside.read_bytes() == content


def test_user_modified_schema_is_not_overwritten(tmp_path):
    from src.storage.app_templates import seed_app_templates
    from src.sdk import HybridDB
    seed_dir = _seed_dir(tmp_path)
    root = tmp_path / "data"
    seed_app_templates("u1", data_root=root, seed_dir=seed_dir)
    app = root / "Users" / "u1" / "Apps" / "tasks"
    db = HybridDB(str(app), embedding_model_name="all-MiniLM-L6-v2")
    try:
        db.create_table("items", {"title": "TEXT", "status": "TEXT", "due": "TEXT", "notes": "TEXT"})
    finally:
        db.close()
    _seed_dir(tmp_path, extra=True)
    assert seed_app_templates("u1", data_root=root, seed_dir=seed_dir) == []
    db = HybridDB(str(app), embedding_model_name="all-MiniLM-L6-v2")
    try:
        assert "notes" in db.get_schema("items")
        assert "priority" not in db.get_schema("items")
    finally:
        db.close()
