"""Local acceptance harness: real tools/governance, no cloud provider or HTTP."""
import json
import os
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
import requests

from examples.jen_reference.fixture import initialise_state
from examples.jen_reference.instance import prepare_instance

PACKAGE = Path(__file__).resolve().parents[2] / "examples/jen_reference"
IMAGE = "example.invalid/assistant@sha256:" + "a" * 64


@pytest.fixture
def harness(tmp_path, monkeypatch):
    import src.sdk.governance as gov
    import src.storage.paths as paths_mod
    from src.config import reload_settings
    from src.sdk.tools_custom import get_custom_tools

    paths = prepare_instance(PACKAGE, tmp_path / "instance with spaces", instance_id="one",
                             owner_label="fixture", engine_image=IMAGE)
    db = paths.data / ".fixture/state.sqlite3"
    initialise_state(db, paths.data / "domain.json")
    for key in list(os.environ):
        if any(part in key for part in ("API_KEY", "LANGFUSE", "OTEL", "OPENAI", "ANTHROPIC")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(paths.data))
    monkeypatch.setenv("GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("GOVERNANCE_PERMISSIONS", json.dumps({"tools": {
        "fixture_store_read": "allow", "fixture_store_pause": "ask"}}))
    monkeypatch.setenv("TOOLS_NATIVE_MODE", "none")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("SCHEDULING_SUBAGENT_ENABLED", "false")
    monkeypatch.setenv("VERIFICATION_ENABLED", "false")
    # Installed shell commands use `python`; expose only the existing test interpreter.
    import sys
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(gov, "_services", {})
    paths_mod._paths_cache.clear()
    reload_settings()
    attempts = []
    def no_network(*args, **kwargs):
        attempts.append("network")
        raise AssertionError("network forbidden in reference acceptance")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(httpx.Client, "request", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "request", no_network)
    monkeypatch.setattr(requests.Session, "request", no_network)
    # Keep real sandbox execution, but refuse unrelated commands in this harness.
    original_popen = subprocess.Popen
    def fixture_only(argv, *args, **kwargs):
        command = " ".join(map(str, argv)) if isinstance(argv, (list, tuple)) else str(argv)
        if argv != ["which", "python"] and ("fixture.py" not in command or str(paths.data / "Tools") not in command):
            raise AssertionError("unrelated subprocess refused")
        return original_popen(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", fixture_only)
    try:
        yield paths, get_custom_tools("default_user"), gov.get_governance_service("default_user"), db
        assert attempts == []
    finally:
        monkeypatch.undo()
        paths_mod._paths_cache.clear()
        reload_settings()
