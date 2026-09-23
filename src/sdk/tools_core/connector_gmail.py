"""Permission-gated Gmail connector actions."""

from __future__ import annotations

from src.sdk.tools import ToolAnnotations, ToolDefinition, ToolResult
from src.storage.gmail_client import GmailClient, GmailNotConnectedError


async def _connector_gmail_send(
    to: str,
    subject: str,
    body: str,
    user_id: str = "",
) -> ToolResult:
    """Send one plain-text Gmail message for the active user."""
    if not user_id:
        return ToolResult(
            content="Gmail send requires a user id.",
            structured_content={"connector": "gmail", "error": "missing_user_id"},
            is_error=True,
        )

    client = GmailClient(user_id=user_id)
    try:
        sent = await client.send_message(to, subject, body)
        message_id = str(sent.get("id") or "")
        if not message_id:
            return ToolResult(
                content="Gmail accepted the request without returning a message id.",
                structured_content={"connector": "gmail", "error": "missing_message_id"},
                is_error=True,
            )
        return ToolResult(
            content=f"Gmail message sent: {message_id}",
            structured_content={
                "connector": "gmail",
                "provider_message_id": message_id,
                "thread_id": sent.get("threadId"),
            },
        )
    except GmailNotConnectedError as exc:
        return ToolResult(
            content=str(exc),
            structured_content={"connector": "gmail", "error": "not_connected"},
            is_error=True,
        )
    except ValueError as exc:
        return ToolResult(
            content=str(exc),
            structured_content={"connector": "gmail", "error": "invalid_request"},
            is_error=True,
        )
    except Exception as exc:
        return ToolResult(
            content=f"Gmail send failed: {exc}",
            structured_content={"connector": "gmail", "error": "provider_error"},
            is_error=True,
        )
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()


connector_gmail_send = ToolDefinition(
    name="connector_gmail_send",
    description="Send a plain-text message through the connected Gmail account.",
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Message subject."},
            "body": {"type": "string", "description": "Plain-text message body."},
            "user_id": {"type": "string", "description": "Active user id."},
        },
        "required": ["to", "subject", "body"],
    },
    annotations=ToolAnnotations(
        requires_approval=True,
        destructive=True,
        idempotent=False,
        open_world=True,
    ),
    function=_connector_gmail_send,
)
