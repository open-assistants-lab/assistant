"""D2 review F3: Gemini key validation and secret redaction on the error path.

`/settings/test-key` reported every Gemini key as "Cannot test provider type"
because `GeminiProvider` exposed only a private client; and the Gemini request
carries the key as a URL query parameter, so error text needed redaction.
"""

from __future__ import annotations

import asyncio

import pytest


def test_gemini_provider_exposes_a_client_for_key_testing():
    from src.sdk.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="AIza-test")
    try:
        assert provider.get_client() is not None
    finally:
        asyncio.run(provider.aclose())


def test_key_test_error_redacts_the_secret():
    from src.http.routers.settings import _key_test_error

    verdict = _key_test_error(
        500, "GET https://example.invalid/v1beta/models?key=AIza-SECRET failed",
        secret="AIza-SECRET",
    )
    assert "AIza-SECRET" not in verdict["error"]
    assert "***" in verdict["error"]


@pytest.mark.asyncio
async def test_test_key_reaches_the_gemini_http_path(monkeypatch):
    """A real GeminiProvider must take the HTTP branch, not "cannot test"."""
    from src.http.routers import settings as settings_mod
    from src.sdk.providers.gemini import GeminiProvider

    class _Response:
        status_code = 200
        text = "{}"

    class _Client:
        async def get(self, url: str) -> _Response:
            assert url.endswith("/models?key=AIza-secret")
            return _Response()

    provider = GeminiProvider(api_key="AIza-secret")
    monkeypatch.setattr(provider, "_get_client", lambda: _Client())
    monkeypatch.setattr(
        settings_mod, "_setup_provider_key_test", lambda p, k: (provider, "gemini")
    )

    verdict = await settings_mod.test_api_key(
        settings_mod.TestKeyRequest(provider="gemini", api_key="AIza-secret")
    )
    assert verdict == {"valid": True}
