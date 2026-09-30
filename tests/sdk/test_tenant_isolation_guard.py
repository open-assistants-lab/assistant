"""#40 — refuse the isolation assumption we do not actually provide.

Deployment mode 3 is documented as "container per user = OS-level isolation".
It is only true if the operator deploys it that way, and nothing enforces it:
per-user directories are separated by path name, with no per-tenant uid.

So we observe how many distinct users one process has served. A process serving
one user is almost certainly a per-user container and is untouched. A process
serving several is *not* one, so an unbounded shell is refused — a cap on the
capability, not on the users, because `DEPLOYMENT.md` explicitly supports a
family or small team on one host.
"""

from __future__ import annotations

import pytest

from src.sdk.tenant_observation import (
    is_multi_user_process,
    observe_user,
    reset,
    users_served,
)


@pytest.fixture(autouse=True)
def _clean_observations():
    reset()
    yield
    reset()


def test_one_user_is_not_multi_user() -> None:
    observe_user("alice")
    assert users_served() == {"alice"}
    assert is_multi_user_process() is False


def test_two_users_is_multi_user() -> None:
    observe_user("alice")
    observe_user("bob")
    assert is_multi_user_process() is True


def test_repeat_observations_of_one_user_do_not_count() -> None:
    for _ in range(20):
        observe_user("alice")
    assert is_multi_user_process() is False


def test_empty_user_id_is_ignored() -> None:
    observe_user(None)
    observe_user("")
    assert users_served() == set()
    assert is_multi_user_process() is False


# --------------------------------------------------------------------------
# the governance cap, exercised through the real policy path
# --------------------------------------------------------------------------


@pytest.fixture
def policy(monkeypatch):
    """Set admin permissions via the real settings object, then restore."""
    from src.config import reload_settings

    def _set(value):
        settings = reload_settings()
        settings.governance.permissions = {"tools": {"shell_execute": value}}
        return settings

    yield _set
    reload_settings().governance.permissions = {}


def _resolve(tool: str, user: str = "alice") -> str:
    from src.sdk.governance import GovernanceService

    return GovernanceService(
        data_root="/tmp/does-not-matter"
    ).resolve_permission_for_call(user, tool, {})


def test_admin_allow_is_honoured_for_a_single_user_process(policy) -> None:
    policy("allow")
    observe_user("alice")
    assert _resolve("shell_execute") == "allow"


def test_admin_allow_is_capped_once_one_process_serves_several_users(policy) -> None:
    policy("allow")
    observe_user("alice")
    observe_user("bob")
    assert _resolve("shell_execute") == "ask"


def test_admin_deny_is_preserved_not_weakened_by_the_cap(policy) -> None:
    """The cap only ever lowers an allow; it must never raise a deny."""
    policy("deny")
    observe_user("alice")
    observe_user("bob")
    assert _resolve("shell_execute") == "deny"


def test_ask_is_left_alone(policy) -> None:
    policy("ask")
    observe_user("alice")
    observe_user("bob")
    assert _resolve("shell_execute") == "ask"


def test_the_cap_applies_only_to_shell_execute(policy) -> None:
    policy("allow")
    observe_user("alice")
    observe_user("bob")
    assert _resolve("files_read") == "allow"


def test_the_escape_hatch_is_explicit_and_defaults_safe(policy) -> None:
    """An operator with isolation provided outside this process must opt in."""
    from src.config import reload_settings

    policy("allow")
    observe_user("alice")
    observe_user("bob")
    assert _resolve("shell_execute") == "ask"

    reload_settings().governance.allow_shell_when_multi_user = True
    assert _resolve("shell_execute") == "allow"


def test_the_registry_is_bounded_and_keeps_the_answer_not_the_roster() -> None:
    """Process-lifetime state must not retain identifiers without limit."""
    for i in range(200):
        observe_user(f"user-{i}")
    assert is_multi_user_process() is True
    # The decision is kept; the identifiers are not.
    assert len(users_served()) <= 64
