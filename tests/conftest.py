"""Root test conftest: isolate the test process from external services.

Set BEFORE any src import (this file is imported first for every test
session under tests/):

- Telemetry: config.yaml ships langfuse.enabled: true, and app_logging's
  load_dotenv() walks up the directory tree — from a worktree it loads the
  main checkout's .env with real keys, making every test export spans to the
  production Langfuse instance (observed: span-batch HTTP retries during
  test_provider_options, issue #15). Tests must be hermetic: telemetry off,
  no keys. Production defaults are untouched.
"""

import os

os.environ["LANGFUSE_ENABLED"] = "false"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["LANGFUSE_BASE_URL"] = ""
os.environ["LANGFUSE_HOST"] = ""
