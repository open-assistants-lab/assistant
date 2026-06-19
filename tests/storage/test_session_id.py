"""Test session_id wiring through MessageStore."""

import pytest
from src.storage.messages import MessageStore


def test_add_message_with_session_id(tmp_path):
    store = MessageStore(base_dir=str(tmp_path), user_id="test_user")
    msg_id = store.add_message("user", "hello", session_id="session_abc")
    msgs = store.get_messages(session_id="session_abc")
    assert len(msgs) == 1
    assert msgs[0].content == "hello"


def test_add_message_without_session_id_still_works(tmp_path):
    store = MessageStore(base_dir=str(tmp_path), user_id="test_user")
    msg_id = store.add_message("user", "hello")
    msgs = store.get_messages()
    assert len(msgs) == 1


def test_session_isolation(tmp_path):
    store = MessageStore(base_dir=str(tmp_path), user_id="test_user")
    store.add_message("user", "session_a_msg", session_id="session_a")
    store.add_message("user", "session_b_msg", session_id="session_b")
    a_msgs = store.get_messages(session_id="session_a")
    b_msgs = store.get_messages(session_id="session_b")
    assert len(a_msgs) == 1
    assert len(b_msgs) == 1
    assert a_msgs[0].content == "session_a_msg"
    assert b_msgs[0].content == "session_b_msg"
