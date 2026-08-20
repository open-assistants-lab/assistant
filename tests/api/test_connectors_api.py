"""Connectors API contract tests — protect the Tools page data shapes.

The native chat app's Tools page parses these exact JSON shapes (ConnectorRow
in native-sdk-experiment/src/main.zig). If any field here changes, the Zig
parser must be updated in lockstep.
"""

import pytest

FIXTURE_SPEC = """name: fixture-api
display: Fixture API
icon: fixture
category: test
description: Fixture connector
auth:
  type: api_key
  required_fields:
  - name: api_key
    label: API Key
    placeholder: sk-...
    input_type: password
    optional: false
"""


@pytest.fixture
def fixture_spec_dir(tmp_path, monkeypatch):
    """Point ConnectKit at a temp spec dir containing only fixture-api."""
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    (spec_dir / "fixture-api.yaml").write_text(FIXTURE_SPEC)
    monkeypatch.setenv("CONNECTKIT_SPEC_DIR", str(spec_dir))
    return spec_dir


def test_catalog_lists_fixture_with_connected_false(client, fixture_spec_dir, test_user_id):
    r = client.get("/connectors/catalog", params={"user_id": test_user_id})
    assert r.status_code == 200
    item = next(c for c in r.json() if c["name"] == "fixture-api")
    assert item["auth_type"] == "api_key"
    assert item["connected"] is False
    # Contract fields the Zig parser reads (ConnectorRow shape).
    assert item["display"] == "Fixture API"
    assert item["category"] == "test"
    assert item["description"] == "Fixture connector"
    fields = item["required_fields"]
    assert fields[0]["name"] == "api_key"
    assert fields[0]["label"] == "API Key"
    assert fields[0]["placeholder"] == "sk-..."
    assert fields[0]["input_type"] == "password"
    assert fields[0]["optional"] is False
    assert "help_text" in fields[0]


def test_api_key_connect_and_disconnect(client, fixture_spec_dir, test_user_id):
    r = client.post(
        "/connectors/connect",
        params={"service": "fixture-api", "user_id": test_user_id},
        json={"api_key": "sk-test"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "connected"

    r = client.get("/connectors/catalog", params={"user_id": test_user_id})
    assert next(c for c in r.json() if c["name"] == "fixture-api")["connected"] is True

    r = client.delete(
        "/connectors/disconnect",
        params={"service": "fixture-api", "user_id": test_user_id},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "disconnected"


def test_connect_missing_credentials_400(client, fixture_spec_dir, test_user_id):
    r = client.post(
        "/connectors/connect",
        params={"service": "fixture-api", "user_id": test_user_id},
        json={},
    )
    assert r.status_code == 400


def test_connect_accepts_control_character_values(client, fixture_spec_dir, test_user_id):
    """The Zig client escapes control chars as \\u00XX; the router must accept
    the decoded values (regression lock for the Tools page JSON escaping)."""
    r = client.post(
        "/connectors/connect",
        params={"service": "fixture-api", "user_id": test_user_id},
        json={"api_key": "sk\x01\x08\x0ctest"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "connected"
    r = client.delete(
        "/connectors/disconnect",
        params={"service": "fixture-api", "user_id": test_user_id},
    )
    assert r.status_code == 200
