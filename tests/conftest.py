"""Root test conftest: isolate the test process from external services.

Set BEFORE any src import (this file is imported first for every test
session under tests/):

- Telemetry: config.yaml ships langfuse.enabled: true, and app_logging's
  load_dotenv() walks up the directory tree — from a worktree it loads the
  main checkout's .env with real keys, making every test export spans to the
  production Langfuse instance (observed: span-batch HTTP retries during
  test_provider_options, issue #15). Tests must be hermetic: telemetry off,
  no keys. Production defaults are untouched.

- Network: the models.dev registry fetch is stubbed at the module boundary.
  A cache-TTL knob is NOT enough — a cold cache still invokes _fetch_api and
  performs a real urlopen. Returning None is safe by contract: the registry
  falls back to its stale disk cache or the built-in subset. Registry unit
  tests override this stub with their own monkeypatches (they patch
  _fetch_api directly).
"""

import os

os.environ["LANGFUSE_ENABLED"] = "false"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["LANGFUSE_BASE_URL"] = ""
os.environ["LANGFUSE_HOST"] = ""


def _registry_fetch_stub() -> None:
    """Test-session stub: no outbound models.dev fetch from tests."""
    return None


_registry_fetch_stub.__test_stub__ = True  # type: ignore[attr-defined]

# The import must stay after the env setup above (E402 expected): the stub
# only exists to be bound onto the module object once it is imported.
import src.sdk.registry as _registry_mod  # noqa: E402

_registry_mod._fetch_api = _registry_fetch_stub


# ---------------------------------------------------------------------------
# Per-test reset of process-global tenant observation
# ---------------------------------------------------------------------------
# src.sdk.tenant_observation is deliberately process-lifetime: the server must
# remember every user it has served, and that history is exactly what makes the
# #40 shell cap safe. That makes it global mutable state, so without a reset any
# test that touches two user_ids poisons every later test in the same process —
# which is how an existing policy test started failing for an unrelated reason.
import pytest


@pytest.fixture(autouse=True)
def _reset_tenant_observation():
    from src.sdk.tenant_observation import reset

    reset()
    yield
    reset()
