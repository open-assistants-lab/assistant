from typing import Any

from fastapi import APIRouter

from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("")
async def list_contacts(user_id: str =  DEFAULT_USER_ID) -> dict[str, Any]:
    """List all contacts."""
    from src.sdk.tools_core.contacts import contacts_list

    result = contacts_list.invoke({"user_id": user_id})
    return {"contacts": result}


@router.get("/search")
async def search_contacts(query: str, user_id: str =  DEFAULT_USER_ID) -> dict[str, Any]:
    """Search contacts."""
    from src.sdk.tools_core.contacts import contacts_search

    result = contacts_search.invoke({"user_id": user_id, "query": query})
    return {"results": result}


@router.post("")
async def add_contact(
    email: str,
    name: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    user_id: str =  DEFAULT_USER_ID,
) -> dict[str, Any]:
    """Add a new contact."""
    from src.sdk.tools_core.contacts import contacts_add

    result = contacts_add.invoke(
        {"user_id": user_id, "email": email, "name": name, "phone": phone, "company": company}
    )
    return {"result": str(result)}


@router.put("/{contact_id}")
async def update_contact(
    contact_id: str,
    email: str | None = None,
    name: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    user_id: str =  DEFAULT_USER_ID,
) -> dict[str, Any]:
    """Update a contact."""
    from src.sdk.tools_core.contacts import contacts_update

    result = contacts_update.invoke(
        {
            "user_id": user_id,
            "contact_id": contact_id,
            "email": email,
            "name": name,
            "phone": phone,
            "company": company,
        }
    )
    return {"result": str(result)}


@router.delete("/{contact_id}")
async def delete_contact(contact_id: str, user_id: str =  DEFAULT_USER_ID) -> dict[str, Any]:
    """Delete a contact."""
    from src.sdk.tools_core.contacts import contacts_delete

    result = contacts_delete.invoke({"user_id": user_id, "contact_id": contact_id})
    return {"result": str(result)}
