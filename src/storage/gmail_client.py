"""Gmail REST client backed by ConnectKit OAuth tokens (Sign in with Google).

Replaces the gws CLI subprocess path with direct Gmail API calls using a
per-user access token from the encrypted ConnectKit vault. Token refresh
(expiry-driven and 401-driven) goes through the same injected HTTP transport,
so the client is fully hermetic in tests and container-friendly in prod.

Usage:
    client = GmailClient(user_id)
    if not client.is_connected():
        ...  # user must Sign in with Google via the ConnectKit flow
    msgs = await client.list_messages(query="from:client")
"""

from __future__ import annotations

from typing import Any, cast

import httpx
from connectkit.bridge import ConnectKitBridge
from connectkit.spec import ConnectorSpec
from connectkit.vault import CredentialVault

from src.app_logging import get_logger

logger = get_logger()

BASE_URL = "https://gmail.googleapis.com/gmail/v1"

SERVICE = "gmail"


class GmailNotConnectedError(Exception):
    """Raised when no usable Gmail credential exists for the user."""


class GmailClient:
    """Thin Gmail API client. All requests route through one injected transport."""

    def __init__(
        self,
        user_id: str = "default_user",
        spec_dir: str | None = None,
        vault_path: str | None = None,
        bridge: ConnectKitBridge | None = None,
        http: httpx.AsyncClient | None = None,
    ):
        self.user_id = user_id
        if bridge is not None:
            self._bridge = bridge
        else:
            from connectkit.bridge import ConnectKitBridge

            self._bridge = ConnectKitBridge(user_id, spec_dir=spec_dir, vault_path=vault_path)
        self._http = http or httpx.AsyncClient(timeout=30)

    @property
    def vault(self) -> CredentialVault:
        return self._bridge.vault

    def is_connected(self) -> bool:
        return bool(self.vault.is_connected(SERVICE))

    # ── token lifecycle ───────────────────────────────────────────────────────

    def _gmail_spec(self) -> ConnectorSpec | None:
        for spec in self._bridge.runtime.get_specs():
            if spec.name == SERVICE:
                return spec
        return None

    async def _refresh_token(self, spec: ConnectorSpec, token: dict[str, Any]) -> dict[str, Any]:
        refresh = token.get("refresh_token")
        if not refresh:
            raise GmailNotConnectedError(
                "Token expired and has no refresh_token — reconnect."
            )
        oauth2 = spec.auth.oauth2
        if oauth2 is None:
            raise GmailNotConnectedError("gmail connector spec has no oauth2 config.")

        body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": token.get("client_id", ""),
        }
        if token.get("client_secret"):
            body["client_secret"] = token["client_secret"]

        resp = await self._http.post(oauth2.token_url, data=body)
        resp.raise_for_status()
        new_token: dict[str, Any] = cast(dict[str, Any], resp.json())
        # Google does not re-issue refresh tokens on refresh; preserve it.
        if "refresh_token" not in new_token:
            new_token["refresh_token"] = refresh
        new_token["client_id"] = token.get("client_id", "")
        new_token["client_secret"] = token.get("client_secret", "")
        self.vault.store_token(SERVICE, "oauth2", new_token)
        logger.info("gmail.token_refreshed", {}, user_id=self.user_id)
        return new_token

    async def _access_token(self, force_refresh: bool = False) -> str:
        spec = self._gmail_spec()
        if spec is None:
            raise GmailNotConnectedError("gmail connector spec not found.")
        token = self.vault.get_token(SERVICE)
        if not token:
            raise GmailNotConnectedError(
                "Gmail is not connected — Sign in with Google first."
            )
        if force_refresh or self.vault.is_expired(SERVICE):
            token = await self._refresh_token(spec, token)
        access = str(token.get("access_token") or "")
        if not access:
            raise GmailNotConnectedError(
                "Gmail token has no access_token — reconnect."
            )
        return access

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        resp = await self._http.get(BASE_URL + path, params=params or {}, headers=headers)
        if resp.status_code == 401:
            # Token revoked/expired server-side: force a refresh and retry once.
            headers = {"Authorization": f"Bearer {await self._access_token(force_refresh=True)}"}
            resp = await self._http.get(BASE_URL + path, params=params or {}, headers=headers)
        resp.raise_for_status()
        return resp

    # ── Gmail API surface ─────────────────────────────────────────────────────

    async def list_messages(
        self,
        max_results: int = 50,
        query: str | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List message ids (+ nextPageToken)."""
        params: dict[str, Any] = {"userId": "me", "maxResults": min(max_results, 500)}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        resp = await self._get("/users/me/messages", params)
        return cast(dict[str, Any], resp.json())

    async def get_message(self, message_id: str, fmt: str = "metadata") -> dict[str, Any]:
        """Fetch one message; fmt: metadata | full | raw."""
        resp = await self._get(
            f"/users/me/messages/{message_id}", {"format": fmt}
        )
        return cast(dict[str, Any], resp.json())

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download one attachment body."""
        resp = await self._get(f"/users/me/messages/{message_id}/attachments/{attachment_id}")
        return resp.content
