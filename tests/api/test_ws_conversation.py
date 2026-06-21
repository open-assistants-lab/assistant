"""Tests for WebSocket conversation runtime behavior."""

from src.sdk.messages import StreamChunk


def test_ws_user_message_sent_to_agent_once(client, monkeypatch, test_user_id):
    seen_messages = []

    async def fake_run_sdk_agent_stream(**kwargs):
        seen_messages.extend(kwargs["messages"])
        yield StreamChunk.done("ok")

    monkeypatch.setattr(
        "src.http.routers.ws.run_sdk_agent_stream", fake_run_sdk_agent_stream
    )

    with client.websocket_connect("/ws/conversation") as websocket:
        websocket.send_json(
            {
                "type": "user_message",
                "content": "hello once",
                "user_id": test_user_id,
            }
        )

        while True:
            msg = websocket.receive_json()
            if msg["type"] == "done":
                break

    user_contents = [m.content for m in seen_messages if m.role == "user"]
    assert user_contents.count("hello once") == 1



