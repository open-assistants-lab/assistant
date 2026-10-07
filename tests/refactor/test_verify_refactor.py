"""The developer verifier must not pass host credentials or strand owned children."""

import socket

import pytest


def test_runner_scrubs_inherited_credentials_and_storage(tmp_path):
    from scripts.verify_refactor import verification_environment

    root = tmp_path / "repo"
    scratch = tmp_path / "scratch"
    result = verification_environment(
        root,
        scratch,
        {
            "PATH": "/test/python/bin",
            "HOME": "/real/home",
            "OPENAI_API_KEY": "private",
            "API_KEY": "private",
            "GH_TOKEN": "private",
            "AWS_ACCESS_KEY_ID": "private",
            "DATABASE_URL": "postgresql://private",
            "HTTP_PROXY": "https://private",
            "DEPLOYMENT_DATA_ROOT": "/customer",
            "LANGFUSE_ENABLED": "true",
        },
    )
    assert result["PATH"] == "/test/python/bin"
    assert result["HOME"] == str(scratch / "home")
    assert result["DEPLOYMENT_DATA_ROOT"] == str(scratch / "data_root")
    assert result["LANGFUSE_ENABLED"] == "false"
    assert not any(value == "private" for value in result.values())
    assert "DATABASE_URL" not in result
    assert "HTTP_PROXY" not in result
    assert "/customer" not in result.values()
    assert str(root) in result["PYTHONPATH"]
    assert result["UV_OFFLINE"] == "1"
    assert result["PIP_NO_INDEX"] == "1"
    # Test-owned recording exporters must work; the network guard, not a global
    # SDK kill switch, prevents real destinations during these unit tests.
    assert "OTEL_SDK_DISABLED" not in result


def test_guard_denies_external_dns_and_tcp():
    from tests.refactor.offline_guard import allowed_host

    assert allowed_host("127.0.0.1") is True
    assert allowed_host("::1") is True
    assert allowed_host("example.com") is False
    assert allowed_host("10.0.0.1") is False


def test_guard_denies_known_live_local_model_ports():
    from tests.refactor.offline_guard import check_address

    for address in [("127.0.0.1", 11434), ("localhost", 8080)]:
        with pytest.raises(OSError):
            check_address(socket.AF_INET, address)
    check_address(socket.AF_INET, ("127.0.0.1", 49153))
