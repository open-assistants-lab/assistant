"""Contacts tools — SDK-native implementation."""

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.sdk.tools_core.contacts_storage import add_contact as storage_add_contact
from src.sdk.tools_core.contacts_storage import delete_contact as storage_delete_contact
from src.sdk.tools_core.contacts_storage import get_contacts, get_contacts_count, search_contacts
from src.sdk.tools_core.contacts_storage import update_contact as storage_update_contact

logger = get_logger()


@tool
def contacts_list(limit: int = 50, user_id: str = "") -> str:
    """List contacts.

    Args:
        limit: Max contacts to return (default: 50)
        user_id: User ID (REQUIRED)

    Returns:
        Formatted list of contacts
    """
    if not user_id:
        return "Error: user_id is required."

    contacts = get_contacts(user_id, limit=limit)
    total = get_contacts_count(user_id)

    if not contacts:
        return (
            "No contacts yet. Contacts will be automatically parsed from your emails during sync."
        )

    output = f"Contacts ({len(contacts)} of {total}):\n\n"
    for i, c in enumerate(contacts, 1):
        name = c.get("name") or c.get("email", "")
        email = c.get("email", "")
        company = c.get("company", "")
        phone = c.get("phone", "")

        output += f"{i}. {name}\n"
        output += f"   Email: {email}\n"
        if company:
            output += f"   Company: {company}\n"
        if phone:
            output += f"   Phone: {phone}\n"
        output += "\n"

    return output.strip()


contacts_list.annotations = ToolAnnotations(title="List Contacts", read_only=True, idempotent=True)


@tool
def contacts_add(
    email: str,
    name: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    user_id: str = "",
) -> str:
    """Add a new contact manually.

    Args:
        email: Contact email address (REQUIRED)
        name: Contact name
        phone: Phone number
        company: Company name
        user_id: User ID (REQUIRED)

    Returns:
        Success or error message
    """
    if not user_id:
        return "Error: user_id is required."

    if not email:
        return "Error: email is required."

    result = storage_add_contact(user_id, email, name, phone, company)

    if result.get("success"):
        return f"Contact added: {name or email} ({email})"
    else:
        return f"Error: {result.get('error')}"


contacts_add.annotations = ToolAnnotations(title="Add Contact")


@tool
def contacts_update(
    email: str | None = None,
    contact_id: str | None = None,
    name: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    user_id: str = "",
) -> str:
    """Update a contact.

    Args:
        email: Contact email address
        contact_id: Contact ID
        name: New name
        phone: New phone number
        company: New company name
        user_id: User ID (REQUIRED)

    Returns:
        Success or error message
    """
    if not user_id:
        return "Error: user_id is required."

    if not email and not contact_id:
        return "Error: email or contact_id is required."

    result = storage_update_contact(user_id, contact_id, email, name, phone, company)

    if result.get("success"):
        return "Contact updated successfully."
    else:
        return f"Error: {result.get('error')}"


contacts_update.annotations = ToolAnnotations(title="Update Contact")


@tool
def contacts_delete(
    email: str | None = None, contact_id: str | None = None, user_id: str = ""
) -> str:
    """Delete a contact.

    Args:
        email: Contact email address
        contact_id: Contact ID
        user_id: User ID (REQUIRED)

    Returns:
        Success or error message
    """
    if not user_id:
        return "Error: user_id is required."

    if not email and not contact_id:
        return "Error: email or contact_id is required."

    result = storage_delete_contact(user_id, contact_id, email)

    if result.get("success"):
        return "Contact deleted successfully."
    else:
        return f"Error: {result.get('error')}"


contacts_delete.annotations = ToolAnnotations(title="Delete Contact", destructive=True)


@tool
def contacts_search(query: str, limit: int = 20, user_id: str = "") -> str:
    """Search contacts by name, email, or company.

    Args:
        query: Search query
        limit: Max results (default: 20)
        user_id: User ID (REQUIRED)

    Returns:
        Matching contacts
    """
    if not user_id:
        return "Error: user_id is required."

    if not query:
        return "Error: query is required."

    contacts = search_contacts(user_id, query, limit)

    if not contacts:
        return f"No contacts found matching '{query}'."

    output = f"Found {len(contacts)} contacts:\n\n"
    for i, c in enumerate(contacts, 1):
        name = c.get("name") or c.get("email", "")
        email = c.get("email", "")
        company = c.get("company", "")

        output += f"{i}. {name} - {email}"
        if company:
            output += f" ({company})"
        output += "\n"

    return output.strip()


contacts_search.annotations = ToolAnnotations(
    title="Search Contacts", read_only=True, idempotent=True
)
