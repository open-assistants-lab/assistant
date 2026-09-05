"""IdentityResolver protocol and UserIdentity value type (roadmap P0-T1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from fastapi import Request

TrustDomain = Literal["desktop", "solo", "trusted-network", "untrusted"]


@dataclass(frozen=True)
class UserIdentity:
    """Resolved identity for an authenticated request.

    - `user_id`: the identity's user (shared-secret impl uses the default
      user; per-user key mapping lands in Phase 2).
    - `key_id`: which credential authenticated the request (None for solo).
    - `trust_domain`: solo (no auth), trusted-network (shared secret),
      untrusted (per-user keys, Phase 2).
    """

    user_id: str | None
    key_id: str | None
    trust_domain: TrustDomain
    # Bug-hunt P1 (billing): scopes carried so admin gates can check them
    # ("admin" scope on per-user keys); shared-secret operator identity has
    # none (admin via trust_domain instead).
    scopes: tuple[str, ...] = ()
    # T3.2 RBAC: org-tree role for per-user identities ("owner"/"admin"/
    # "staff"); shared-secret/operator identity is owner-equivalent ("" here,
    # admin gates treat trust_domain != "untrusted" as owner-equivalent).
    role: str = "staff"


@runtime_checkable
class IdentityResolver(Protocol):
    """Resolve a request to an identity, or None if unauthenticated.

    Implementations may be sync or async; the middleware awaits the result
    when it is awaitable.
    """

    def resolve(self, request: Request) -> UserIdentity | None:  # pragma: no cover
        """Return the identity for `request`, or None to reject with 401."""
        ...
