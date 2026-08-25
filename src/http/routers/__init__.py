from src.http.routers.contacts import router as contacts_router
from src.http.routers.conversation import router as conversation_router
from src.http.routers.email import router as email_router
from src.http.routers.health import router as health_router
from src.http.routers.improvements import router as improvements_router
from src.http.routers.memories import router as memories_router
from src.http.routers.profile import router as profile_router
from src.http.routers.scheduler import router as scheduler_router
from src.http.routers.settings import router as settings_router
from src.http.routers.skills import router as skills_router
from src.http.routers.subagents import router as subagents_router
from src.http.routers.todos import router as todos_router
from src.http.routers.tools import router as tools_router
from src.http.routers.user_prompt import router as user_prompt_router
from src.http.routers.webhooks import router as webhooks_router
from src.http.routers.workspace import router as workspace_router
from src.http.routers.workspaces import router as workspaces_router

__all__ = [
    "health_router",
    "scheduler_router",
    "contacts_router",
    "conversation_router",
    "email_router",
    "memories_router",
    "profile_router",
    "todos_router",
    "workspace_router",
    "workspaces_router",
    "user_prompt_router",
    "skills_router",
    "settings_router",
    "subagents_router",
    "tools_router",
    "webhooks_router",
    "improvements_router",
]
