"""Readiness is installed content + state + matching authenticated runtime catalog."""
import dataclasses
import json
from pathlib import Path

import httpx
import pytest
import yaml

from examples.jen_reference import readiness
from examples.jen_reference.fixture import initialise_state
from examples.jen_reference.instance import InstancePaths, prepare_instance

PACKAGE = Path(__file__).resolve().parents[2] / "examples/jen_reference"
IMAGE = "example.invalid/assistant@sha256:" + "a" * 64


@pytest.fixture
def instance(tmp_path):
    paths = prepare_instance(PACKAGE, tmp_path / "instance", instance_id="one",
                             owner_label="fixture", engine_image=IMAGE)
    initialise_state(paths.data / ".fixture/state.sqlite3", paths.data / "domain.json")
    return paths


def runtime(monkeypatch, handler):
    original = httpx.Client
    def make_client(**kwargs):
        assert kwargs["timeout"] == 5
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(readiness.httpx, "Client", make_client)


def test_static_valid_is_not_runtime_ready(instance):
    result = readiness.check_readiness(instance)
    assert not result.ready
    assert result.checks["runtime"] == "not_checked"
    assert all(result.checks[k] == "ok" for k in ("package", "configuration", "profile", "tools", "fixture_state"))


@pytest.mark.parametrize("name", [".env", "data/PROFILE.md", "data/Tools/fixture_store_pause/TOOL.md", "data/.fixture/state.sqlite3"])
def test_missing_required_content_not_ready(instance, name):
    (instance.root / name).unlink()
    assert not readiness.check_readiness(instance).ready


def test_authenticated_health_and_matching_catalog(instance, monkeypatch):
    key = next(line.partition("=")[2] for line in instance.env_file.read_text().splitlines() if line.startswith("API_KEY="))
    seen = []
    def handle(request):
        seen.append(request.url.path)
        assert request.headers["authorization"] == f"Bearer {key}"
        data = {"status": "ok"} if request.url.path == "/health" else {"tools": [
            {"name": "fixture_store_read", "source": "custom", "enabled": True},
            {"name": "fixture_store_pause", "source": "custom", "enabled": True},
            {"name": "files_read", "source": "native", "enabled": True}], "categories": {}}
        return httpx.Response(200, json=data)
    runtime(monkeypatch, handle)
    result = readiness.check_readiness(instance, runtime_url="http://127.0.0.1:8080")
    assert result.ready and seen == ["/health", "/tools"]
    assert key not in json.dumps(dataclasses.asdict(result))


@pytest.mark.parametrize("status,data", [(200, {"tools": []}), (302, {}), (401, {}), (200, {"wrong": "shape"}), (200, {"tools": [None]}), (200, {"tools": {"wrong": "shape"}})])
def test_health_alone_or_redirect_never_ready(instance, monkeypatch, status, data):
    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(status, json=data, headers={"location": "https://remote.invalid/"})
    runtime(monkeypatch, handle)
    assert not readiness.check_readiness(instance, runtime_url="http://[::1]:8080").ready


@pytest.mark.parametrize("url", ["https://remote.invalid", "http://localhost:8080", "http://127.0.0.1@remote.invalid", "http://secret@127.0.0.1", "file:///tmp/x", "http://127.0.0.1/x?token=secret", "http://127.0.0.1:bad"])
def test_invalid_or_nonliteral_remote_url_refused(instance, url, monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError("invalid URL must not open client")
    monkeypatch.setattr(readiness.httpx, "Client", forbidden)
    result = readiness.check_readiness(instance, runtime_url=url)
    assert not result.ready and result.checks["runtime"] == "invalid_url"
    assert "secret" not in json.dumps(dataclasses.asdict(result))


def test_timeout_and_secret_like_error_are_redacted(instance, monkeypatch):
    def handle(request):
        raise httpx.ReadTimeout("credential=secret@example.invalid")
    runtime(monkeypatch, handle)
    result = readiness.check_readiness(instance, runtime_url="http://127.0.0.1:8080")
    assert not result.ready and result.checks["runtime"] == "unavailable"
    assert "credential" not in json.dumps(dataclasses.asdict(result))


@pytest.mark.parametrize("field,value", [("engine_image", "different@sha256:" + "b" * 64), ("package_version", "9.0.0")])
def test_image_or_package_mismatch(instance, field, value):
    meta = json.loads(instance.metadata.read_text())
    meta[field] = value
    instance.metadata.write_text(json.dumps(meta))
    assert readiness.check_readiness(instance).checks["package"] != "ok"


@pytest.mark.parametrize("frontmatter", ["- name: wrong", "unexpected-scalar", "42", "name: [wrong]", "name: {value: wrong}"])
def test_malformed_tool_metadata_returns_bounded_checks(instance, frontmatter):
    tool = instance.data / "Tools/fixture_store_read/TOOL.md"
    tool.write_text("---\n" + frontmatter + "\n---\ninvalid fixture tool")
    result = readiness.check_readiness(instance)
    assert not result.ready and result.checks["tools"] == "invalid"


def test_readiness_symlink_never_reads_target(instance, tmp_path, monkeypatch):
    link = tmp_path / "link"
    link.symlink_to(instance.root, target_is_directory=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("symlink target must never be read")
    monkeypatch.setattr(Path, "read_text", forbidden)
    result = readiness.check_readiness(InstancePaths.at(link))
    assert not result.ready


def test_compose_offline_and_scoped():
    compose = yaml.safe_load((PACKAGE / "compose.yaml").read_text())
    assert list(compose["services"]) == ["assistant"]
    service = compose["services"]["assistant"]
    assert "container_name" not in service
    assert service["pull_policy"] == "never"
    assert service["ports"] == ["127.0.0.1:${INSTANCE_PORT:?instance port required}:8080"]
    assert service["environment"]["SOLO_BYPASS"] == "false"
    assert compose["networks"]["fixture"]["internal"] is True
    assert service["env_file"] == [".env"]
    mounts = service["volumes"]
    assert all("docker.sock" not in m["source"] for m in mounts)
    assert any(m["target"] == "/app/data/PROFILE.md" and m["read_only"] for m in mounts)
    assert all(m["read_only"] for m in mounts if m["target"] in {
        "/app/data/Tools", "/app/data/fixture.py", "/app/data/domain.json", "/app/config.yaml"})
    assert service["command"] == ["/app/.venv/bin/assistant", "http"]
