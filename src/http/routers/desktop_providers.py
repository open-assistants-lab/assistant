"""Desktop v0.1 provider contracts (Phase D2, task 1).

Local-first key classification, selected-provider validation, the
consent-gated Check-likely-providers flow, and loopback-only local model
discovery. Design constraints (D0 decisions + plan §Phase D2):

- Key classification is a pure local shape check — never sent anywhere.
- Check-likely-providers is consent-gated: the REQUEST must affirmatively
  ask (``consent: true``); there is no automatic multi-provider probe.
- Local model discovery is restricted to a fixed loopback allowlist
  (Ollama, LM Studio, generic OpenAI-compatible). The Assistant sidecar
  port and every non-loopback host are rejected.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.http.auth import enforce_user_id
from src.http.routers.settings import TestKeyRequest
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/providers", tags=["desktop-providers"])

# Fixed loopback discovery allowlist (D0 task 3). Hosts: loopback only.
# Ports: Ollama 11434, LM Studio 1234, generic OpenAI-compatible 8000/8080.
_ALLOWLIST_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ALLOWLIST_PORTS = {11434, 1234, 8000, 8080}
_loopback = re.compile(
    r"^http://(127\.0\.0\.1|localhost|\[::1\])(?::(\d+))?$"
)


def _endpoint_allowed(endpoint: str) -> bool:
    m = _loopback.match((endpoint or "").strip().rstrip("/"))
    if m is None:
        return False
    port = int(m.group(2) or 80)
    return port in _ALLOWLIST_PORTS


class _Http:
    """Module-level indirection so tests can patch the transport."""

    get = staticmethod(httpx.get)


_http = _Http()


# -- local key classification (pure, never networked) -----------------------


class _ClassifyRequest(BaseModel):
    key: str = Field(min_length=8)


_KEY_SHAPES: tuple[tuple[str, str], ...] = (
    ("sk-ant-", "anthropic"),
    ("sk-or-", "openrouter"),
    ("sk-proj-", "openai"),
    ("sk-", "openai"),
    ("AIza", "gemini"),
    ("r8_", "groq"),
    ("gsk_", "groq"),
)


@router.post("/classify-key")
def classify_key(body: _ClassifyRequest) -> dict[str, str]:
    """High-confidence local key classification.

    Pure shape sniffing — the key is never transmitted or validated against
    any provider here (that is /validate-key or the consent-gated
    /check-likely flow).
    """
    key = body.key.strip()
    provider = "unknown"
    for prefix, name in _KEY_SHAPES:
        if key.startswith(prefix):
            provider = name
            break
    return {
        "provider": provider,
        "confidence": "high" if provider != "unknown" else "low",
        "key_prefix": key[:8],
    }


# -- selected-provider validation (selected provider only) ------------------


class _ValidateRequest(BaseModel):
    provider: str
    api_key: str


@router.post("/validate-key")
async def validate_key(body: _ValidateRequest) -> dict[str, Any]:
    """Validate the pasted key against the SELECTED provider only."""
    verdict = await _probe_provider(body.provider, body.api_key)
    return {"provider": body.provider, **verdict}


# -- consent-gated Check-likely-providers -----------------------------------


async def _probe_provider(provider: str, api_key: str) -> dict[str, Any]:
    """Validate a key against one provider via the settings test-key machinery."""
    from src.http.routers.settings import test_api_key

    return await test_api_key(
        TestKeyRequest(provider=provider, api_key=api_key)
    )


class _CheckLikelyRequest(BaseModel):
    key: str
    consent: bool = False


@router.post("/check-likely")
async def check_likely(body: _CheckLikelyRequest) -> dict[str, Any]:
    """Explicit consent-gated candidate check (D0: no automatic probe)."""
    if not body.consent:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail=(
                "Check-likely-providers requires affirmative consent "
                "('consent': true) — the key is tested against the reviewed "
                "candidate providers only."
            ),
        )
    # Candidates derive from the key shape's most likely providers, ordered.
    candidates: list[str] = []
    key = body.key.strip()
    for prefix, name in _KEY_SHAPES:
        if key.startswith(prefix) and name not in candidates:
            candidates.append(name)

    results = []
    seen: set[str] = set()
    for provider in candidates:
        if provider in seen:
            continue
        seen.add(provider)
        results.append({"provider": provider, **_probe_provider(provider, key)})
    return {"checked": list(seen), "results": results}


# -- loopback-only local model discovery ------------------------------------


class _ModelsResponse(BaseModel):
    models: list[dict[str, str]]


@router.get("/local-models")
def local_models(endpoint: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    enforce_user_id(
        user_id, None
    )  # desktop: identity is server-side; endpoint is local-only
    if not _endpoint_allowed(endpoint):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail=(
                f"endpoint {endpoint!r} is not in the loopback discovery "
                "allowlist (127.0.0.1/localhost on ports 11434, 1234, 8000, 8080)"
            ),
        )
    try:
        resp = _http.get(f"{endpoint.strip().rstrip('/')}/v1/models", timeout=3.0)
        data = resp.json().get("data", [])
    except Exception:
        return {"models": [], "reachable": False}
    models = [
        {"id": str(m.get("id", "")), "name": str(m.get("name") or m.get("id", ""))}
        for m in data
        if isinstance(m, dict) and m.get("id")
    ]
    return {"models": models, "reachable": True}


class _ValidateEndpointRequest(BaseModel):
    endpoint: str


@router.post("/validate-endpoint")
def validate_endpoint(body: _ValidateEndpointRequest) -> dict[str, Any]:
    if not _endpoint_allowed(body.endpoint):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail=(
                f"endpoint {body.endpoint!r} is not in the loopback discovery "
                "allowlist (127.0.0.1/localhost on ports 11434, 1234, 8000, 8080)"
            ),
        )
    try:
        _http.get(f"{body.endpoint.strip().rstrip('/')}/v1/models", timeout=3.0)
    except Exception:
        return {"reachable": False, "endpoint": body.endpoint}
    return {"reachable": True, "endpoint": body.endpoint}


# -- available-model listing (catalog snapshot) ------------------------------


@router.get("/models")
def models() -> dict[str, Any]:
    from src.http.routers.settings import _catalog_snapshot

    providers, models = _catalog_snapshot()
    return {"providers": providers, "models": models}
