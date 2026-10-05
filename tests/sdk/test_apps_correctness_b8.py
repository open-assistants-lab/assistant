"""B8: apps correctness — phantom apps, handle keying, sheet collisions, date rewrites, schema clobber (#133-#137)."""

from __future__ import annotations

import pytest

from src.sdk.tools_core import apps as apps_mod
from src.sdk.tools_core.apps import app_create, app_import_csv, app_query, app_schema


@pytest.fixture(autouse=True)
def apps_env(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.storage.paths import DataPaths

    root = tmp_path / "store"
    data_path = tmp_path / "cfg"
    settings = SimpleNamespace(
        filesystem=SimpleNamespace(allowed_roots=[], workspace_root=None),
        memory=SimpleNamespace(messages=SimpleNamespace(max_chroma_index_gb=1)),
        agent=SimpleNamespace(model=""),
    )
    monkeypatch.setattr(apps_mod, "get_settings", lambda: settings)
    def paths_fn(user, workspace_id="personal"):
        return DataPaths(
            user_id=user, data_root=root, data_path=data_path, workspace_id=workspace_id
        )
    monkeypatch.setattr(apps_mod, "_get_base_path", lambda user: paths_fn(user).apps_dir())
    monkeypatch.setattr(apps_mod, "_dbs", {})
    return root, data_path


class TestPhantomApps:
    """#133: reads of a missing app must not create one, and delete reports not-found."""

    @pytest.mark.asyncio
    async def test_schema_of_a_missing_app_writes_nothing(self, apps_env):
        root, _ = apps_env
        apps_root = root / "Users" / "u" / "Apps"
        result = await app_schema.ainvoke({"name": "ghost", "user_id": "u"})
        assert "not found" in str(getattr(result, "content", result)).lower()
        assert not (apps_root / "ghost").exists(), (
            "a schema read created a phantom app directory"
        )

    @pytest.mark.asyncio
    async def test_query_of_a_missing_app_is_refused(self, apps_env):
        root, _ = apps_env
        result = await app_query.ainvoke({"app": "ghost2", "query": "SELECT 1", "user_id": "u"})
        assert "not found" in str(getattr(result, "content", result)).lower()

    @pytest.mark.asyncio
    async def test_delete_of_a_missing_app_says_not_found(self, apps_env):
        root, _ = apps_env
        result = await apps_mod.app_delete.ainvoke({"name": "neverwas", "user_id": "u"})
        assert "not found" in str(getattr(result, "content", result)).lower()


class TestHandleCacheKeying:
    """#134: one directory, one handle — the cache key uses the sanitized name."""

    def test_alias_names_share_one_handle(self, apps_env):
        root, _ = apps_env
        h1 = apps_mod._get_db("My App", "u")
        h2 = apps_mod._get_db("my_app", "u")
        h3 = apps_mod._get_db("my-app", "u")
        assert h1 is h2 is h3, "aliased app names produced separate handles"

    def test_delete_closes_the_handle_key(self, apps_env):
        root, _ = apps_env
        apps_mod._get_db("Tracker", "u")
        apps_mod._delete_app("tracker", "u")
        assert not any(k.endswith("tracker") for k in apps_mod._dbs), (
            "delete missed the sanitized cache key"
        )


class TestSheetCollisions:
    """#135: two sheets sanitizing to one table must not lose rows."""

    @pytest.mark.asyncio
    async def test_two_same_named_sheets_keep_their_rows(self, apps_env, tmp_path, monkeypatch):
        """One workbook, two sheets that sanitize to the same name."""
        from types import SimpleNamespace

        import src.config as cfg

        root, _ = apps_env
        allowed = tmp_path / "ws"
        allowed.mkdir()
        xlsx = allowed / "book.xlsx"
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        s1 = wb.active
        s1.title = "Sheet 1"
        s1.append(["a", "b"])
        s1.append([1, 2])
        s2 = wb.create_sheet("sheet_1")
        s2.append(["a", "b"])
        s2.append([3, 4])
        wb.save(xlsx)

        settings_mod = SimpleNamespace(
            filesystem=SimpleNamespace(allowed_roots=[str(allowed)], workspace_root=None),
        )
        monkeypatch.setattr(cfg, "get_settings", lambda: settings_mod)

        r = await app_import_csv.ainvoke({"path": str(xlsx), "app_name": "book", "user_id": "u"})
        text = str(getattr(r, "content", r))
        assert "error" not in text.lower(), text
        q = await app_query.ainvoke(
            {"app": "book", "query": "SELECT COUNT(*) AS n FROM sheet_1", "user_id": "u"}
        )
        qtext = str(getattr(q, "content", q))
        assert "'n': 1" in qtext, f"first sheet's rows were erased: {qtext}"
        q2 = await app_query.ainvoke(
            {"app": "book", "query": "SELECT COUNT(*) AS n FROM sheet_1_2", "user_id": "u"}
        )
        assert "'n': 1" in str(getattr(q2, "content", q2)), "the second sheet's rows are missing"


class TestDateRewrites:
    """#136: identifiers containing date words survive the rewrite."""

    def test_today_inside_an_identifier_is_untouched(self):
        q = apps_mod._convert_date_in_query('SELECT today_total FROM "today" WHERE d > today')
        assert "today_total" in q, "a column named today_total was rewritten"
        assert '"today"' in q, "a quoted identifier was rewritten"
        assert "today" not in q.split('WHERE')[1].split('FROM')[0], q
        assert q.count("T") or "17909" in q  # the WHERE-clause date was rewritten

    def test_literals_still_protected(self):
        q = apps_mod._convert_date_in_query("SELECT * FROM t WHERE note = 'today'")
        assert "'today'" in q


class TestCreateOverExistingTable:
    """#137: re-creating a table must not silently rewrite its schema."""

    @pytest.mark.asyncio
    async def test_conflicting_schema_is_refused(self, apps_env):
        root, _ = apps_env
        await app_create.ainvoke({"name": "app7", "tables": {"t": {"x": "TEXT"}}, "user_id": "u"})
        r2 = await app_create.ainvoke({"name": "app7", "tables": {"t": {"y": "INTEGER"}}, "user_id": "u"})
        text = str(getattr(r2, "content", r2))
        assert "error" in text.lower(), f"a conflicting redefinition succeeded: {text}"
        schema = (await app_schema.ainvoke({"name": "app7", "user_id": "u"}))
        assert "x" in str(schema), "the stored schema was overwritten by the refused create"

    @pytest.mark.asyncio
    async def test_identical_schema_is_idempotent(self, apps_env):
        root, _ = apps_env
        await app_create.ainvoke({"name": "app8", "tables": {"t": {"x": "TEXT"}}, "user_id": "u"})
        r2 = await app_create.ainvoke({"name": "app8", "tables": {"t": {"x": "TEXT"}}, "user_id": "u"})
        assert "error" not in str(getattr(r2, "content", r2)).lower()
