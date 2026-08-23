"""Unit tests for IMAP flag parsing (audit B7).

imap_tools >=1.x returns ``msg.flags`` as a tuple of flag strings
(``('SEEN', '\\Flagged')``). The old code checked ``hasattr(msg.flags,
"Seen")`` — always False on a tuple — so every email was stored with
``read=True`` and ``flagged=False``, and unread/urgent counting in the
agent scheduler always reported zero.
"""

from src.sdk.tools_core.email_db import parse_email_flags


class FakeMsg:
    """Minimal stand-in exposing only the flags tuple."""

    def __init__(self, flags: tuple[str, ...]) -> None:
        self.flags = flags


def test_flag_detection_from_imap_tuple():
    msg = FakeMsg(("SEEN", "\\Flagged"))
    assert parse_email_flags(msg) == {"read": True, "flagged": True}


def test_unread_unflagged():
    msg = FakeMsg(())
    assert parse_email_flags(msg) == {"read": False, "flagged": False}


def test_read_only():
    msg = FakeMsg(("SEEN",))
    assert parse_email_flags(msg) == {"read": True, "flagged": False}


def test_flagged_only_is_unread_and_flagged():
    msg = FakeMsg(("\\Flagged",))
    assert parse_email_flags(msg) == {"read": False, "flagged": True}


def test_lowercase_flags_are_recognized():
    # Some servers report lowercase or unprefixed variants.
    msg = FakeMsg(("seen", "flagged"))
    assert parse_email_flags(msg) == {"read": True, "flagged": True}


def test_recent_flag_does_not_trigger_flagged():
    msg = FakeMsg(("SEEN", "RECENT"))
    assert parse_email_flags(msg) == {"read": True, "flagged": False}
