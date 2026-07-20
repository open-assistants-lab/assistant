"""Tests for conversation model listing metadata."""

from unittest.mock import patch

import pytest

from src.http.routers.conversation import list_available_models


@pytest.mark.asyncio
async def test_models_includes_hosted_agnes_when_env_key_set():
    with patch.dict("os.environ", {"AGNES_API_KEY": "sk-test"}, clear=False):
        result = await list_available_models(user_id="alice")

    agnes = next(m for m in result["models"] if m["id"] == "agnes:agnes-2.0-flash")
    assert agnes["name"] == "Agnes 2.0 Flash"
    assert agnes["provider"] == "agnes"
    assert agnes["provider_display"] == "Agnes"
    assert agnes["key_source"] == "hosted"
    assert agnes["billing_mode"] == "hosted"


@pytest.mark.asyncio
async def test_models_marks_agnes_user_key_before_hosted_key():
    with (
        patch.dict("os.environ", {"AGNES_API_KEY": "sk-hosted"}, clear=False),
        patch("src.http.routers.conversation._stored_provider_key", return_value="sk-user"),
    ):
        result = await list_available_models(user_id="alice")

    agnes = next(m for m in result["models"] if m["id"] == "agnes:agnes-2.0-flash")
    assert agnes["key_source"] == "user"
    assert agnes["billing_mode"] == "user"


@pytest.mark.asyncio
async def test_models_marks_env_key_billing_mode_as_env():
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-openai"}, clear=False),
        patch("src.http.routers.conversation._stored_provider_key", return_value=None),
    ):
        result = await list_available_models(user_id="alice")

    openai = next(m for m in result["models"] if m["provider"] == "openai")
    assert openai["key_source"] == "env"
    assert openai["billing_mode"] == "env"
