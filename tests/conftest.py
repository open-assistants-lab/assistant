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

import src.sdk.registry as _registry_mod

_registry_mod._fetch_api = _registry_fetch_stub