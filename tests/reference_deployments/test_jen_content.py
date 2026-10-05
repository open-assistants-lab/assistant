"""Catch unsafe/malformed reference content before installation."""
import json
from pathlib import Path

import yaml
from agentprofile import load_profile

from src.sdk.tools_custom import load_tool_meta

PACKAGE = Path(__file__).resolve().parents[2] / "examples/jen_reference"


def test_profiles_and_tools_parse():
    profile = load_profile(PACKAGE / "PROFILE.md")
    assert profile.name == "jen-reference"
    domain = json.loads((PACKAGE / "domain.json").read_text())
    assert [s["id"] for s in domain["stores"]] == ["fixture-alpha", "fixture-beta"]
    for name in ("fixture_store_read", "fixture_store_pause"):
        meta = load_tool_meta(PACKAGE / "Tools" / name / "TOOL.md")
        assert meta and meta["name"] == name
        assert "store_id" in meta["parameters"]["required"]
        assert not meta.get("install")
        assert not {"url", "script", "sql"} & meta["parameters"]["properties"].keys()


def test_write_requires_explicit_approval():
    meta = load_tool_meta(PACKAGE / "Tools/fixture_store_pause/TOOL.md")
    assert meta is not None
    assert meta["annotations"]["requires_approval"] is True
    assert meta["annotations"]["read_only"] is False
    assert meta["parameters"]["properties"]["paused"]["enum"] == ["true", "false"]
    config = yaml.safe_load((PACKAGE / "config.yaml").read_text())
    assert config["governance"]["permissions"]["tools"]["fixture_store_pause"] == "ask"
    assert config["tools"]["native"]["mode"] == "none"
    assert config["scheduling"]["subagent_enabled"] is False


def test_no_customer_endpoints_or_data():
    assert PACKAGE.exists()
    for p in PACKAGE.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            text = p.read_text()
            assert "gongchatea.com.au" not in text
            assert "host.docker.internal" not in text
            assert "zii-portal/session" not in text


def test_no_default_usable_secrets():
    env = (PACKAGE / ".env.example").read_text()
    for line in env.splitlines():
        if line.startswith("API_KEY="):
            assert line == "API_KEY="
    assert "ASSISTANT_IMAGE=" in env
    assert ":latest" not in env
