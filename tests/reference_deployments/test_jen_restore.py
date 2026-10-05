"""Exercise the runbook's stopped-copy recipe, not a backup daemon/API."""
import gc
import json
from pathlib import Path

import pytest

from examples.jen_reference.fixture import read_store, set_pause
from examples.jen_reference.instance import validate_instance
from src.config import reload_settings
from src.sdk.governance import GovernanceService
from src.sdk.tools_custom import get_custom_tools
from tests.reference_deployments.test_jen_governance import USER, propose

PACKAGE = Path(__file__).resolve().parents[2] / "examples/jen_reference"
IMAGE = "example.invalid/assistant@sha256:" + "a" * 64


@pytest.fixture
def recipe():
    text = (PACKAGE / "README.md").read_text()
    code = text.split("```python\n", 1)[1].split("```", 1)[0]
    namespace = {}
    exec(compile(code, "stopped-copy-recipe", "exec"), namespace)
    return namespace


async def test_stopped_restore_preserves_state_pending_and_saved_history(harness, recipe, tmp_path, monkeypatch):
    source, _, svc, db = harness
    ids, messages = await propose(harness)
    svc.approve(USER, ids[0])
    set_pause(db, "fixture-beta", True, 1)
    history = source.data / "saved-history.json"
    history.write_text(json.dumps([m.model_dump(mode="json") for m in messages]))
    # Governance opens short-lived connections; no kernels ran before snapshot.
    gc.collect()
    snapshot = tmp_path / "snapshot"
    recipe["snapshot_stopped"](source, snapshot)
    dest = recipe["restore_stopped"](PACKAGE, snapshot, tmp_path / "restored",
                                    instance_id="restored", owner_label="new-fixture-owner", engine_image=IMAGE)
    assert validate_instance(dest) == []
    assert dest.env_file.read_text() != source.env_file.read_text()
    assert (dest.data / "saved-history.json").read_text() == history.read_text()
    restored_db = dest.data / ".fixture/state.sqlite3"
    assert read_store(restored_db, "fixture-beta")["revision"] == 2
    other = GovernanceService(data_root=str(dest.data))
    assert other.get_pending(USER, ids[0])["status"] == "approved"
    assert not (dest.root / "data.initial").exists()
    # The recipe changes both installed config and the execution-time policy.
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(dest.data))
    monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"fixture_store_pause":"deny"}}')
    reload_settings()
    result = await other.execute_approved(USER, ids[0], registry=get_custom_tools(USER))
    assert result["outcome"] == "refused"
    assert read_store(restored_db, "fixture-alpha")["paused"] is False
    assert read_store(db, "fixture-alpha")["revision"] == 1
    assert json.loads(dest.metadata.read_text())["recovery_mode"] == "writes_denied"


def test_snapshot_excludes_keys_and_rejects_symlinks(harness, recipe, tmp_path):
    source = harness[0]
    snapshot = tmp_path / "snapshot"
    recipe["snapshot_stopped"](source, snapshot)
    key = next(x[8:] for x in source.env_file.read_text().splitlines() if x.startswith("API_KEY="))
    assert all(key.encode() not in p.read_bytes() for p in snapshot.rglob("*") if p.is_file())
    (source.data / "leak").symlink_to(source.env_file)
    with pytest.raises(ValueError):
        recipe["snapshot_stopped"](source, tmp_path / "bad-snapshot")
    assert not (tmp_path / "bad-snapshot").exists()


@pytest.mark.parametrize("mismatch", ["checksum", "version", "image"])
def test_mismatch_refused_before_destination_creation(harness, recipe, tmp_path, mismatch):
    snapshot = tmp_path / "snapshot"
    recipe["snapshot_stopped"](harness[0], snapshot)
    if mismatch == "checksum":
        (snapshot / "data/domain.json").write_text("corrupt")
    else:
        meta = json.loads((snapshot / "source.json").read_text())
        meta["package_version" if mismatch == "version" else "engine_image"] = "wrong"
        (snapshot / "source.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        recipe["restore_stopped"](PACKAGE, snapshot, tmp_path / "restore", instance_id="new",
                                  owner_label="fixture", engine_image=IMAGE)
    assert not (tmp_path / "restore").exists()


def test_restore_never_overwrites_existing(harness, recipe, tmp_path):
    snapshot = tmp_path / "snapshot"
    recipe["snapshot_stopped"](harness[0], snapshot)
    dest = tmp_path / "existing"
    dest.mkdir()
    (dest / "keep").write_text("keep")
    with pytest.raises(ValueError):
        recipe["restore_stopped"](PACKAGE, snapshot, dest, instance_id="new", owner_label="fixture", engine_image=IMAGE)
    assert (dest / "keep").read_text() == "keep"
