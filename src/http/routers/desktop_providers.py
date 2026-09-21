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
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
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

# A prefix shared by more than one vendor family cannot be a high-confidence
# match on its own (generic ``sk-`` is OpenAI's classic shape, but many
# OpenAI-compatible vendors use it too).
_AMBIGUOUS_PREFIXES = frozenset({"sk-"})


def _shape_matches(key: str) -> list[tuple[str, str]]:
    """Longest-prefix-only shape matches for *key*.

    ``sk-ant-…`` matches both ``sk-ant-`` (Anthropic) and the generic
    ``sk-`` (OpenAI). Only the longest match counts, so a provider-specific
    key is never offered to a vendor that merely shares a shorter prefix
    (D2 review F2).
    """
    matches = [(prefix, name) for prefix, name in _KEY_SHAPES if key.startswith(prefix)]
    if not matches:
        return []
    longest = max(len(prefix) for prefix, _ in matches)
    return [(prefix, name) for prefix, name in matches if len(prefix) == longest]


def _shape_candidates(key: str) -> list[str]:
    """Ordered, de-duplicated provider candidates for *key*."""
    candidates: list[str] = []
    for _, name in _shape_matches(key):
        if name not in candidates:
            candidates.append(name)
    return candidates


def _shape_confidence(key: str) -> str:
    matches = _shape_matches(key)
    if not matches or matches[0][0] in _AMBIGUOUS_PREFIXES:
        return "low"
    return "high"


@router.post("/classify-key")
def classify_key(body: _ClassifyRequest) -> dict[str, Any]:
    """High-confidence local key classification.

    Pure shape sniffing — the key is never transmitted or validated against
    any provider here (that is /validate-key or the consent-gated
    /check-likely flow). ``candidates`` names every provider that could own
    the key (most specific first) so the client can preview before probing.
    """
    key = body.key.strip()
    candidates = _shape_candidates(key)
    return {
        "provider": candidates[0] if candidates else "unknown",
        "confidence": _shape_confidence(key),
        "candidates": candidates,
        # Bounded display prefix: an 8-character key must not be echoed whole.
        "key_prefix": key[:4] + ("…" if len(key) > 4 else ""),
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
    # Explicit client-approved candidates (memo §4.2: the API accepts the
    # reviewed provider IDs and never expands the list itself).
    providers: list[str] | None = None


_PROBEABLE_PROVIDERS = frozenset(name for _, name in _KEY_SHAPES)


def _require_known_provider(provider: str) -> None:
    """Only the reviewed candidate set may be probed (memo §4.2)."""
    if provider not in _PROBEABLE_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider candidate: {provider}",
        )


@router.post("/check-likely")
async def check_likely(body: _CheckLikelyRequest) -> dict[str, Any]:
    """Explicit consent-gated candidate check (D0: no automatic probe).

    The probe list is the client's explicitly approved ``providers`` when
    supplied; otherwise only the single most-specific shape candidate is
    probed. A provider-specific key (``sk-ant-``) is never sent to a vendor
    that merely shares a shorter prefix (``sk-`` → OpenAI) — the pre-fix
    behaviour sent it to both (D2 review F2).
    """
    if not body.consent:
        raise HTTPException(
            status_code=422,
            detail=(
                "Check-likely-providers requires affirmative consent "
                "('consent': true) — the key is tested against the reviewed "
                "candidate providers only."
            ),
        )
    key = body.key.strip()
    candidates = _shape_candidates(key)
    if body.providers is not None:
        approved: list[str] = []
        for provider in body.providers:
            if provider not in approved:
                _require_known_provider(provider)
                approved.append(provider)
    else:
        approved = candidates[:1]

    results = []
    for provider in approved:
        probe = await _probe_provider(provider, key)
        results.append({"provider": provider, **probe})
    return {"candidates": candidates, "checked": approved, "results": results}


# -- loopback-only local model discovery ------------------------------------


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


def _manual_endpoint_error(endpoint: str) -> str | None:
    """Shape check for an explicitly typed endpoint (decision A, 2026-09-21).

    Automatic discovery stays allowlisted (``local-models``); a URL the user
    typed is validated as-is, so a vLLM on 127.0.0.1:5678 or a LAN host works.
    """
    parsed = urlparse((endpoint or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return f"endpoint {endpoint!r} must be a full http(s) URL"
    return None


@router.post("/validate-endpoint")
def validate_endpoint(body: _ValidateEndpointRequest) -> dict[str, Any]:
    error = _manual_endpoint_error(body.endpoint)
    if error:
        raise HTTPException(status_code=422, detail=error)
    try:
        _http.get(f"{body.endpoint.strip().rstrip('/')}/v1/models", timeout=3.0)
    except Exception:
        return {"reachable": False, "endpoint": body.endpoint}
    return {"reachable": True, "endpoint": body.endpoint}
