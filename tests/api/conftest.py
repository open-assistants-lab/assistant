"""Shared fixtures for API contract tests."""

import os
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("USER_ID", "test_api_user")


@pytest.fixture(scope="session", autouse=True)
def isolated_data_path():
    """Keep API contract tests from reading or deleting local app data."""
    orig_data_path = os.environ.get("DEPLOYMENT_DATA_PATH")
    orig_ea_root = os.environ.get("DEPLOYMENT_EA_ROOT")
    with tempfile.TemporaryDirectory() as data_path:
        os.environ["DEPLOYMENT_DATA_PATH"] = data_path
        os.environ["DEPLOYMENT_EA_ROOT"] = str(Path(data_path) / "ea_root")

        from src.config import reload_settings
        from src.storage.messages import _stores
        from src.storage.paths import _paths_cache

        reload_settings()
        _stores.clear()
        _paths_cache.clear()
        yield data_path
        del os.environ["DEPLOYMENT_EA_ROOT"]
        del os.environ["DEPLOYMENT_DATA_PATH"]
        if orig_data_path is not None:
            os.environ["DEPLOYMENT_DATA_PATH"] = orig_data_path
        if orig_ea_root is not None:
            os.environ["DEPLOYMENT_EA_ROOT"] = orig_ea_root
        reload_settings()


@pytest.fixture(scope="session")
def app(isolated_data_path):
    """Create FastAPI app for testing."""
    from src.http.main import app

    return app


@pytest.fixture(scope="session")
def client(app):
    """Create a TestClient for the FastAPI app."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def test_user_id(request):
    """Return a unique test user ID."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", request.node.name)
    return f"test_api_{os.getpid()}_{safe_name}"


@pytest.fixture
def test_user_id_2(request):
    """Return a second test user ID for isolation tests."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", request.node.name)
    return f"test_api_2_{os.getpid()}_{safe_name}"


@pytest.fixture
def conversation_messages():
    """Sample messages for conversation tests."""
    return [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "What can you do?"},
        {"role": "assistant", "content": "I can help with many tasks."},
    ]
