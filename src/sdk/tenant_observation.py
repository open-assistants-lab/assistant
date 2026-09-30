"""Per-process tenant observation, used to refuse unsafe isolation assumptions.

Motivation (#40). Our deployment story has four shapes, and one of them is not
built:

  1. single user, one device          boundary = the user's OS account
  2. single user, many devices        one uid, one user
  3. multi users, trusted             one container per user, shared secret
  4. multi users, untrusted           one container per user, per-user auth

For 3 and 4 the container count follows the user count: **N users, N
containers.** Trusted vs untrusted is about authentication, not isolation.

So a deployment can end up serving several users from one process with no
separation between them — per-user directories are separated by path name
alone, with no per-tenant uid, `chown` or `chmod` anywhere in the codebase.
From inside a single process we cannot tell whether the other users live in
sibling containers, so this module does not guess.

Instead it observes: **how many distinct users has this process served?** That
number is self-calibrating.

* one process, one user  -> the supported shape for multi-user deployments.
  Nothing is refused; this is a backstop, not the primary control.
* one process, N users   -> this process is NOT a per-user container, so the
  documented isolation does not exist here. The dangerous capability is refused.

The check is deliberately narrow: it refuses a *capability*, not users. Refusing
the second user would break the legitimate "family or small team on one host"
deployment that `DEPLOYMENT.md` explicitly supports.
"""

from __future__ import annotations

import threading

#: Bounded on purpose. The registry is process-lifetime by design — that history
#: is what makes the shell cap safe — but it must not grow without limit or
#: retain identifiers indefinitely. Once we know the answer ("more than one"),
#: the exact set is no longer needed.
_MAX_TRACKED = 64

_lock = threading.Lock()
_seen_users: set[str] = set()
_multi_user = False


def observe_user(user_id: str | None) -> None:
    """Record that this process has served ``user_id``."""
    if not user_id:
        return
    global _multi_user
    with _lock:
        if _multi_user:
            return
        if len(_seen_users) >= _MAX_TRACKED:
            # Cannot still be single-user at this size; stop accumulating ids.
            _multi_user = True
            _seen_users.clear()
            return
        _seen_users.add(str(user_id))
        if len(_seen_users) > 1:
            _multi_user = True
            _seen_users.clear()


def users_served() -> set[str]:
    """Distinct user_ids seen so far.

    Emptied once multi-user is established — we keep the answer, not the roster.
    """
    with _lock:
        return set(_seen_users)


def is_multi_user_process() -> bool:
    """True once this process has served more than one distinct user.

    Before two users are seen this returns False, so a correctly deployed
    per-user container never trips it — including the very first request.
    """
    return _multi_user or len(users_served()) > 1


def reset() -> None:
    """Forget observations. Tests only."""
    global _multi_user
    with _lock:
        _seen_users.clear()
        _multi_user = False
