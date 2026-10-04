"""B1: apps and subagent names must not escape or destroy user data (#131, #132, #109)."""

import pytest

from src.sdk.tools_core.apps import (
    _delete_app,
    _get_app_path,
    app_delete,
    app_import_csv,
)


@pytest.fixture(autouse=True)
def isolated_apps(tmp_path, monkeypatch):
    import src.sdk.tools_core.apps as apps_mod

    root = tmp_path / "store"
    monkeypatch.setattr(
        apps_mod, "_get_base_path", lambda user_id: root / "Users" / user_id / "Apps"
    )
    monkeypatch.setattr(apps_mod, "_dbs", {})
    yield root


class TestEmptyAndDegenerateNames:
    """#132: an empty or sanitising-to-empty name must never target the root."""

    @pytest.mark.parametrize("name", ["", "///", "   ", "***"])
    def test_degenerate_names_are_rejected_before_they_become_paths(self, name):
        from src.sdk.tools_core.apps import _validate_app_name

        with pytest.raises(ValueError):
            _validate_app_name(name)

    def test_ordinary_names_still_resolve(self):
        from src.sdk.tools_core.apps import _validate_app_name

        assert _validate_app_name("My App") == "my_app"

    def test_app_path_is_never_the_apps_root(self, isolated_apps):
        for name in ("", "///", "   "):
            with pytest.raises(ValueError):
                _get_app_path(name, "alice")

    def test_delete_of_a_degenerate_name_does_not_remove_every_app(self, isolated_apps):
        base = isolated_apps / "Users" / "alice" / "Apps"
        keeper = base / "notes"
        keeper.mkdir(parents=True)
        (keeper / "data.txt").write_text("keep me")

        with pytest.raises(ValueError):
            _delete_app("", "alice")

        assert (keeper / "data.txt").read_text() == "keep me"

    @pytest.mark.asyncio
    async def test_app_delete_reports_the_invalid_name(self, isolated_apps):
        result = await app_delete.ainvoke({"name": "", "user_id": "alice"})
        text = str(getattr(result, "content", result))
        assert "name" in text.lower()


class TestImportRespectsTheFilesystemBoundary:
    """#131: app_import_csv must go through the same boundary as files_*."""

    @pytest.mark.asyncio
    async def test_a_path_outside_the_allowed_roots_is_refused(self, tmp_path, isolated_apps):
        outside = tmp_path / "secrets.csv"
        outside.write_text("a,b\n1,2\n")
        result = await app_import_csv.ainvoke(
            {"path": str(outside), "app_name": "importer", "user_id": "alice"}
        )
        text = str(getattr(result, "content", result))
        assert "not allowed" in text.lower() or "allowed roots" in text.lower(), (
            f"a path outside every root was accepted: {text[:120]}"
        )

    @pytest.mark.asyncio
    async def test_a_path_inside_the_allowed_roots_still_imports(self, tmp_path, monkeypatch, isolated_apps):
        from types import SimpleNamespace

        import src.config as cfg

        allowed = tmp_path / "workspace"
        allowed.mkdir()
        csv = allowed / "data.csv"
        csv.write_text("a,b\n1,2\n")
        monkeypatch.setattr(
            cfg,
            "get_settings",
            lambda: SimpleNamespace(
                filesystem=SimpleNamespace(allowed_roots=[str(allowed)], workspace_root=None)
            ),
        )
        result = await app_import_csv.ainvoke(
            {"path": str(csv), "app_name": "importer", "user_id": "alice"}
        )
        text = str(getattr(result, "content", result))
        assert "error" not in text.lower(), f"a permitted import failed: {text[:160]}"


class TestSubagentNamesCannotEscapeTheDirectory:
    """#109: a subagent name is a path segment, so it must be validated everywhere."""

    def test_delete_rejects_a_traversing_name(self, tmp_path, monkeypatch):
        import src.sdk.coordinator as coordinator_mod
        import src.storage.paths as paths_mod

        base = tmp_path / "subagents"
        monkeypatch.setattr(
            paths_mod.DataPaths, "user_subagents_dir", lambda self: base, raising=False
        )
        coordinator = coordinator_mod.SubagentCoordinator(user_id="alice")
        assert coordinator.base_path == base

        victim = tmp_path / "victim"
        victim.mkdir()
        marker = victim / "PROFILE.md"
        marker.write_text("keep")

        import asyncio

        with pytest.raises(Exception):
            asyncio.run(coordinator.delete("../victim"))
        assert marker.exists(), "a traversing name deleted outside the subagents directory"
