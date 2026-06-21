from src.http.routers.companion import router as companion_router
from src.http.routers.contacts import router as contacts_router
from src.http.routers.conversation import router as conversation_router
from src.http.routers.email import router as email_router
from src.http.routers.health import router as health_router
from src.http.routers.memories import router as memories_router
from src.http.routers.settings import router as settings_router
from src.http.routers.skills import router as skills_router
from src.http.routers.subagents import router as subagents_router
from src.http.routers.todos import router as todos_router
from src.http.routers.tools import router as tools_router
from src.http.routers.user_prompt import router as user_prompt_router
from src.http.routers.workspace import router as workspace_router
from src.http.routers.workspaces import router as workspaces_router

__all__ = [
    "health_router",
    "companion_router",
    "contacts_router",
    "conversation_router",
    "email_router",
    "memories_router",
    "todos_router",
    "workspace_router",
    "workspaces_router",
    "user_prompt_router",
    "skills_router",
    "settings_router",
    "subagents_router",
    "tools_router",
]
