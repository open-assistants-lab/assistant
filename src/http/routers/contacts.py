from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("")
async def list_contacts(user_id: str = "default_user") -> dict[str, Any]:
    """List all contacts."""
    from src.sdk.tools_core.contacts import contacts_list

    result = await contacts_list.ainvoke({"user_id": user_id})
    return {"contacts": result}


@router.get("/search")
async def search_contacts(query: str, user_id: str = "default_user") -> dict[str, Any]:
    """Search contacts."""
    from src.sdk.tools_core.contacts import contacts_search

    result = await contacts_search.ainvoke({"user_id": user_id, "query": query})
    return {"results": result}


@router.post("")
async def add_contact(
    email: str,
    name: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    user_id: str = "default_user",
) -> dict[str, Any]:
    """Add a new contact."""
    from src.sdk.tools_core.contacts import contacts_add

    result = await contacts_add.ainvoke(
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
    user_id: str = "default_user",
) -> dict[str, Any]:
    """Update a contact."""
    from src.sdk.tools_core.contacts import contacts_update

    result = await contacts_update.ainvoke(
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


@router.get("/{contact_id}")
async def get_contact(contact_id: str, user_id: str = "default_user") -> dict[str, Any]:
    """Get a contact by ID."""
    from src.sdk.tools_core.contacts import contacts_get

    result = await contacts_get.ainvoke({"user_id": user_id, "contact_id": contact_id})
    return {"contact": result}


@router.delete("/{contact_id}")
async def delete_contact(contact_id: str, user_id: str = "default_user") -> dict[str, Any]:
    """Delete a contact."""
    from src.sdk.tools_core.contacts import contacts_delete

    result = await contacts_delete.ainvoke({"user_id": user_id, "contact_id": contact_id})
    return {"result": str(result)}
