"""C1-1 email_draft: valid EmailMessage delivered via IMAP APPEND, never sent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.sdk.tools_core.email_draft import (
    append_draft,
    build_draft_message,
    email_draft,
)


@pytest.fixture()
def one_account(tmp_path, monkeypatch):
    """One connected IMAP account; isolated paths."""
    import src.storage.paths as paths_mod

    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    from src.sdk.tools_core import email_sync

    monkeypatch.setattr(
        email_sync,
        "_load_accounts",
        lambda user_id: {
            "acct1": {
                "email": "me@example.com",
                "password": "pw",
                "imap_host": "imap.example.com",
                "imap_port": 993,
            }
        },
    )
    import src.sdk.tools_core.email_db as email_db

    monkeypatch.setattr(email_db, "get_account_id_by_name", lambda n, u: "acct1")
    monkeypatch.setattr(paths_mod, "_paths_cache", {}, raising=False)
    yield
    paths_mod._paths_cache.clear()


def test_build_draft_message_is_valid_rfc822():
    msg = build_draft_message("me@example.com", "you@example.com", "Hi", "Body")
    assert msg["From"] == "me@example.com"
    assert msg["To"] == "you@example.com"
    assert msg["Subject"] == "Hi"
    assert "Body" in msg.get_content()


def test_append_draft_issues_imap_append_to_drafts(one_account):
    conn = MagicMock()
    conn.append.return_value = ("OK", [b"[APPENDUID 1 42] Drafts"])
    with patch("imaplib.IMAP4_SSL", return_value=conn) as ssl_cls:
        msg = build_draft_message("me@example.com", "you@example.com", "Hi", "Body")
        handle = append_draft(
            {"email": "me@example.com", "password": "pw",
             "imap_host": "imap.example.com", "imap_port": 993},
            msg,
        )
    ssl_cls.assert_called_once_with("imap.example.com", 993)
    args, _ = conn.append.call_args
    assert args[0] == "Drafts"
    assert "Draft" in args[1]
    payload: bytes = args[3]
    assert b"Subject: Hi" in payload
    assert b"To: you@example.com" in payload
    assert b"From: me@example.com" in payload
    assert handle["status"] == "drafted"
    assert handle["uid"] == "42"


def test_email_draft_tool_happy_path(one_account):
    conn = MagicMock()
    conn.append.return_value = ("OK", [b"[APPENDUID 1 7] Drafts"])
    with patch("imaplib.IMAP4_SSL", return_value=conn):
        out = email_draft.invoke(
            {"account_name": "work", "to": "you@example.com",
             "subject": "Report", "body": "Here it is", "user_id": "u1"},
        )
    import json

    handle = json.loads(out)
    assert handle["status"] == "drafted"
    assert handle["folder"] == "Drafts"


def test_email_draft_tool_no_accounts_is_actionable(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    from src.sdk.tools_core import email_sync

    monkeypatch.setattr(email_sync, "_load_accounts", lambda user_id: {})
    out = email_draft.invoke(
        {"account_name": "", "to": "a@b.c", "subject": "s", "body": "b", "user_id": "u1"}
    )
    assert out.startswith("Error:")
    assert "No email accounts connected" in out


def test_email_draft_tool_missing_creds_is_actionable(one_account):
    from src.sdk.tools_core import email_sync

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            email_sync,
            "_load_accounts",
            lambda user_id: {
                "acct1": {"email": "", "password": "", "imap_host": "", "imap_port": 993}
            },
        )
        out = email_draft.invoke(
            {"account_name": "", "to": "a@b.c", "subject": "s", "body": "b", "user_id": "u1"}
        )
        assert out.startswith("Error:")
        assert "IMAP credentials" in out
    finally:
        monkeypatch.undo()


def test_email_draft_requires_user_id():
    out = email_draft.invoke(
        {"account_name": "", "to": "a@b.c", "subject": "s", "body": "b", "user_id": ""}
    )
    assert "user_id is required" in out
