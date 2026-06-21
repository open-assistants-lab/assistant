"""SDK-native tools registry.

This module serves as the single source of truth for all SDK-native tools.
Tools are registered here as they are migrated to the SDK @tool decorator
(from src.sdk.tools).

The runner's _build_tool_list() calls get_native_tools() which returns all
registered ToolDefinitions. Native tools take priority over adapted LangChain
tools of the same name.
"""

from src.sdk.tools import ToolDefinition, ToolRegistry
from src.sdk.tools_core.apps import (
    app_column_add,
    app_column_delete,
    app_column_rename,
    app_create,
    app_delete,
    app_delete_row,
    app_insert,
    app_list,
    app_query,
    app_schema,
    app_search_fts,
    app_search_hybrid,
    app_search_semantic,
    app_update,
)
from src.sdk.tools_core.browser import (
    browser_back,
    browser_click,
    browser_eval,
    browser_fill,
    browser_forward,
    browser_get_html,
    browser_get_text,
    browser_get_title,
    browser_get_url,
    browser_hover,
    browser_open,
    browser_press,
    browser_screenshot,
    browser_scroll,
    browser_sessions,
    browser_snapshot,
    browser_tab_close,
    browser_tab_new,
    browser_type,
    browser_wait_text,
)
from src.sdk.tools_core.file_search import (
    files_glob_search,
    files_grep_search,
)
from src.sdk.tools_core.file_versioning import (
    files_versions_clean,
    files_versions_delete,
    files_versions_list,
    files_versions_restore,
)
from src.sdk.tools_core.filesystem import (
    files_delete,
    files_edit,
    files_list,
    files_mkdir,
    files_read,
    files_rename,
    files_write,
)
from src.sdk.tools_core.mcp import (
    mcp_list,
    mcp_reload,
    mcp_tools,
)
from src.sdk.tools_core.memory import memory_profile, memory_reflection
from src.sdk.tools_core.message import (
    message_count,
    message_history,
    message_search,
    message_timeline,
)
from src.sdk.tools_core.research import (
    research_list,
    research_start,
)
from src.sdk.tools_core.shell import shell_execute
from src.sdk.tools_core.skills import (
    skills_load,
    skills_reload,
)
from src.sdk.tools_core.subagent import (
    subagent_cancel,
    subagent_check,
    subagent_create,
    subagent_delegate,
    subagent_delete,
    subagent_instruct,
    subagent_list,
    subagent_start,
    subagent_tasks,
    subagent_update,
)
from src.sdk.tools_core.summarize import summarize_session
from src.sdk.tools_core.time import time_get
from src.sdk.tools_core.user_prompt import user_prompt_get, user_prompt_set
from src.sdk.tools_core.web import web_fetch, web_search

_registry = ToolRegistry()


def _register_all() -> None:
    registry = _registry

    registry.register(time_get)
    registry.register(shell_execute)
    registry.register(user_prompt_get)
    registry.register(user_prompt_set)

    registry.register(files_list)
    registry.register(files_read)
    registry.register(files_write)
    registry.register(files_edit)
    registry.register(files_delete)
    registry.register(files_mkdir)
    registry.register(files_rename)
    registry.register(files_glob_search)
    registry.register(files_grep_search)
    registry.register(files_versions_list)
    registry.register(files_versions_restore)
    registry.register(files_versions_delete)
    registry.register(files_versions_clean)

    registry.register(message_search)
    registry.register(message_count)
    registry.register(message_history)
    registry.register(message_timeline)
    registry.register(memory_profile)
    registry.register(memory_reflection)

    registry.register(web_fetch)
    registry.register(web_search)

    registry.register(browser_open)
    registry.register(browser_snapshot)
    registry.register(browser_click)
    registry.register(browser_fill)
    registry.register(browser_type)
    registry.register(browser_press)
    registry.register(browser_scroll)
    registry.register(browser_hover)
    registry.register(browser_screenshot)
    registry.register(browser_eval)
    registry.register(browser_get_title)
    registry.register(browser_get_text)
    registry.register(browser_get_html)
    registry.register(browser_get_url)
    registry.register(browser_tab_new)
    registry.register(browser_tab_close)
    registry.register(browser_back)
    registry.register(browser_forward)
    registry.register(browser_wait_text)
    registry.register(browser_sessions)

    registry.register(app_create)
    registry.register(app_list)
    registry.register(app_schema)
    registry.register(app_delete)
    registry.register(app_insert)
    registry.register(app_update)
    registry.register(app_delete_row)
    registry.register(app_column_add)
    registry.register(app_column_delete)
    registry.register(app_column_rename)
    registry.register(app_query)
    registry.register(app_search_fts)
    registry.register(app_search_semantic)
    registry.register(app_search_hybrid)

    registry.register(subagent_create)
    registry.register(subagent_delegate)
    registry.register(subagent_start)
    registry.register(subagent_check)
    registry.register(subagent_tasks)
    registry.register(subagent_list)
    registry.register(subagent_instruct)
    registry.register(subagent_cancel)
    registry.register(subagent_delete)
    registry.register(subagent_update)
    registry.register(summarize_session)

    registry.register(mcp_list)
    registry.register(mcp_reload)
    registry.register(mcp_tools)

    registry.register(skills_load)
    registry.register(skills_reload)

    registry.register(research_start)
    registry.register(research_list)

    # ConnectKit meta-tools
    try:
        from src.sdk.tools_core.connector_adapter import get_connector_tools

        for td in get_connector_tools():
            registry.register(td)
    except ImportError:
        pass


_register_all()


def get_native_tools() -> list[ToolDefinition]:
    """Return all registered SDK-native ToolDefinitions."""
    return _registry.list_tools()


def get_native_tool_names() -> set[str]:
    """Return the set of all registered SDK-native tool names."""
    return set(_registry.list_names())


# Tool categories derived from naming convention category_verb
CATEGORIES: dict[str, str] = {}


def _derive_category(name: str) -> str:
    """Derive category from tool name (category_verb convention)."""
    if "_" in name:
        return name.split("_")[0]
    return "core"


def get_tool_category(name: str) -> str:
    """Return the category for a given tool name."""
    return CATEGORIES.get(name, _derive_category(name))


def _populate_categories() -> None:
    """Auto-populate CATEGORIES from registered tool names."""
    for name in get_native_tool_names():
        CATEGORIES[name] = _derive_category(name)


_populate_categories()
