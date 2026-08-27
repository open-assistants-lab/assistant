"""GmailClient — ConnectKit-OAuth-backed Gmail REST client tests.

Hermetic: all HTTP goes through an injected httpx.MockTransport, including the
token-refresh exchange, so no network access is needed.
"""

import httpx
import pytest

from src.storage.gmail_client import GmailClient, GmailNotConnectedError

SPEC = """name: gmail
display: Google Gmail
category: email
auth:
  type: oauth2
  oauth2:
    authorize_url: https://accounts.google.com/o/oauth2/v2/auth
    token_url: https://oauth2.googleapis.com/token
    scopes: [https://www.googleapis.com/auth/gmail.readonly]
    pkce: true
"""

VALID_TOKEN = {
    "access_token": "ya29.valid",
    "expires_in": 3600,
    "refresh_token": "1//refresh-abc",
    "client_id": "test-client",
    "client_secret": "test-secret",
}

EXPIRED_TOKEN = {
    "access_token": "ya29.stale",
    "expires_in": -10,  # already expired
    "refresh_token": "1//refresh-abc",
    "client_id": "test-client",
    "client_secret": "test-secret",
}


@pytest.fixture
def gmail_env(tmp_path, monkeypatch):
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    (spec_dir / "gmail.yaml").write_text(SPEC)
    # Fixed vault key so the bridge (writer) and client (reader) share one
    # encryption key; otherwise each CredentialVault instance mints an
    # ephemeral Fernet key and cross-instance reads fail.
    monkeypatch.setenv("CONNECTKIT_VAULT_KEY", "test-vault-key-for-gmail-tests")
    return spec_dir, tmp_path / "vault.db"


def _bridge(spec_dir, vault_path, token: dict | None):
    from connectkit.bridge import ConnectKitBridge

    b = ConnectKitBridge("test-user", spec_dir=str(spec_dir), vault_path=str(vault_path))
    if token is not None:
        b.vault.store_token("gmail", "oauth2", dict(token))
    return b


def _http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _client(spec_dir, vault_path, http) -> GmailClient:
    return GmailClient("test-user", spec_dir=str(spec_dir), vault_path=str(vault_path), http=http)


def test_not_connected_raises(gmail_env):
    spec_dir, vault_path = gmail_env
    _bridge(spec_dir, vault_path, token=None)
    client = _client(spec_dir, vault_path, _http(lambda r: httpx.Response(500, text="unreachable")))
    assert client.is_connected() is False
    with pytest.raises(GmailNotConnectedError, match="Sign in with Google"):
        import asyncio

        asyncio.run(client.list_messages())


@pytest.mark.asyncio
async def test_list_messages_parses_and_uses_token(gmail_env):
    spec_dir, vault_path = gmail_env
    _bridge(spec_dir, vault_path, VALID_TOKEN)

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"messages": [{"id": "msg1"}, {"id": "msg2"}], "nextPageToken": "tok2"})

    client = _client(spec_dir, vault_path, _http(handler))
    result = await client.list_messages(max_results=50, query="from:client")

    assert [m["id"] for m in result["messages"]] == ["msg1", "msg2"]
    assert result["nextPageToken"] == "tok2"
    assert captured["auth"] == "Bearer ya29.valid"
    assert "maxResults=50" in captured["url"]
    assert "from%3Aclient" in captured["url"]  # httpx percent-encodes the query


@pytest.mark.asyncio
async def test_expired_token_refreshes_then_lists(gmail_env):
    spec_dir, vault_path = gmail_env
    bridge = _bridge(spec_dir, vault_path, EXPIRED_TOKEN)
    assert bridge.vault.is_expired("gmail") is True

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/token"):
            # token exchange — assert grant_type/refresh_token present
            assert b"grant_type=refresh_token" in request.content
            assert b"refresh_token=1%2F%2Frefresh-abc" in request.content
            return httpx.Response(200, json={"access_token": "ya29.refreshed", "expires_in": 3600})
        return httpx.Response(200, json={"messages": [{"id": "fresh"}]})

    client = _client(spec_dir, vault_path, _http(handler))
    result = await client.list_messages()

    assert result["messages"][0]["id"] == "fresh"
    # refresh happened first, then the list call used the new token
    assert any("/token" in url for _, url in calls)
    assert bridge.vault.get_token("gmail")["access_token"] == "ya29.refreshed"


@pytest.mark.asyncio
async def test_expired_without_refresh_token_raises(gmail_env):
    spec_dir, vault_path = gmail_env
    no_refresh = {**EXPIRED_TOKEN, "refresh_token": ""}
    _bridge(spec_dir, vault_path, no_refresh)
    client = _client(spec_dir, vault_path, _http(lambda r: httpx.Response(500, text="x")))

    with pytest.raises(GmailNotConnectedError, match="reconnect"):
        await client.list_messages()


@pytest.mark.asyncio
async def test_get_message_metadata(gmail_env):
    spec_dir, vault_path = gmail_env
    _bridge(spec_dir, vault_path, VALID_TOKEN)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "msg1", "threadId": "t1", "payload": {"mimeType": "text/plain"}})

    client = _client(spec_dir, vault_path, _http(handler))
    msg = await client.get_message("msg1", fmt="metadata")

    assert msg["id"] == "msg1"
    assert "/users/me/messages/msg1" in captured["url"]
    assert "format=metadata" in captured["url"]


@pytest.mark.asyncio
async def test_get_attachment_bytes(gmail_env):
    spec_dir, vault_path = gmail_env
    _bridge(spec_dir, vault_path, VALID_TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nraw-bytes")

    client = _client(spec_dir, vault_path, _http(handler))
    data = await client.get_attachment("msg1", "att1")
    assert data == b"\x89PNG\r\n\x1a\nraw-bytes"


@pytest.mark.asyncio
async def test_401_triggers_force_refresh_retry(gmail_env):
    spec_dir, vault_path = gmail_env
    _bridge(spec_dir, vault_path, VALID_TOKEN)  # NOT expired, but server 401s

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Authorization"))
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "ya29.after-401", "expires_in": 3600})
        return httpx.Response(401, json={"error": {"code": 401}})

    client = _client(spec_dir, vault_path, _http(handler))
    with pytest.raises(Exception):
        await client.list_messages()

    # first attempt used the stale token; a refresh was attempted after the 401
    assert calls[0] == "Bearer ya29.valid"
    assert any(c == "Bearer ya29.after-401" for c in calls)
