"""SDK-native tools registry.

This module serves as the single source of truth for all SDK-native tools,
registered with the SDK @tool decorator.

The runner's _build_tool_list() calls get_native_tools() which returns all
registered ToolDefinitions.
"""

from src.sdk.tools import ToolDefinition, ToolRegistry
from src.sdk.tools_core.apps import (
    app_column_add,
    app_column_delete,
    app_column_rename,
    app_create,
    app_delete,
    app_delete_row,
    app_import_csv,
    app_insert,
    app_list,
    app_query,
    app_schema,
    app_search_fts,
    app_summarize,
    app_update,
)
from src.sdk.tools_core.browser import (
    browser_click,
    browser_eval,
    browser_fill,
    browser_open,
    browser_screenshot,
    browser_snapshot,
)
from src.sdk.tools_core.code_execute import code_execute
from src.sdk.tools_core.corpus import index_corpus, search_corpus
from src.sdk.tools_core.design_extractor import design_extract
from src.sdk.tools_core.email_draft import email_draft
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
from src.sdk.tools_core.memory import memory_profile
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
from src.sdk.tools_core.user_prompt import (
    interview_ask,
    interview_finish,
    interview_start,
    user_prompt_get,
    user_prompt_set,
)
from src.sdk.tools_core.web import web_fetch, web_search

_registry = ToolRegistry()


# Desktop v0.1 (D1 task 5): tool families excluded from the desktop
# capability profile — filtered BEFORE registration, so catalogs, storage
# side effects, and schedulers never initialize for them.
DESKTOP_EXCLUDED_FAMILIES = ("email_", "contacts_", "todos_")


def _desktop_filtering_active() -> bool:
    import os

    return os.environ.get("DEPLOYMENT_MODE") == "desktop-server"


def _desktop_excluded(tool_name: str) -> bool:
    return _desktop_filtering_active() and any(
        tool_name.startswith(prefix) for prefix in DESKTOP_EXCLUDED_FAMILIES
    )


def reset_native_tools() -> None:
    """Clear the registry so tests/deployments can re-register under a
    different capability mode."""
    _registry._tools.clear()  # noqa: SLF001 - test/deployment seam


def _register_all() -> None:
    registry = _registry

    if not _desktop_excluded("time_get"):
        registry.register(time_get)
    if not _desktop_excluded("shell_execute"):
        registry.register(shell_execute)
    if not _desktop_excluded("user_prompt_get"):
        registry.register(user_prompt_get)
    if not _desktop_excluded("user_prompt_set"):
        registry.register(user_prompt_set)
    if not _desktop_excluded("interview_start"):
        registry.register(interview_start)
    if not _desktop_excluded("interview_ask"):
        registry.register(interview_ask)
    if not _desktop_excluded("interview_finish"):
        registry.register(interview_finish)

    if not _desktop_excluded("files_list"):
        registry.register(files_list)
    if not _desktop_excluded("files_read"):
        registry.register(files_read)
    if not _desktop_excluded("files_write"):
        registry.register(files_write)
    if not _desktop_excluded("files_edit"):
        registry.register(files_edit)
    if not _desktop_excluded("files_delete"):
        registry.register(files_delete)
    if not _desktop_excluded("files_mkdir"):
        registry.register(files_mkdir)
    if not _desktop_excluded("files_rename"):
        registry.register(files_rename)
    if not _desktop_excluded("files_glob_search"):
        registry.register(files_glob_search)
    if not _desktop_excluded("files_grep_search"):
        registry.register(files_grep_search)
    if not _desktop_excluded("files_versions_list"):
        registry.register(files_versions_list)
    if not _desktop_excluded("files_versions_restore"):
        registry.register(files_versions_restore)
    if not _desktop_excluded("files_versions_delete"):
        registry.register(files_versions_delete)
    if not _desktop_excluded("files_versions_clean"):
        registry.register(files_versions_clean)

    if not _desktop_excluded("message_search"):
        registry.register(message_search)
    if not _desktop_excluded("message_count"):
        registry.register(message_count)
    if not _desktop_excluded("message_history"):
        registry.register(message_history)
    if not _desktop_excluded("message_timeline"):
        registry.register(message_timeline)
    if not _desktop_excluded("memory_profile"):
        registry.register(memory_profile)

    if not _desktop_excluded("index_corpus"):
        registry.register(index_corpus)
    if not _desktop_excluded("search_corpus"):
        registry.register(search_corpus)
    if not _desktop_excluded("code_execute"):
        registry.register(code_execute)
    if not _desktop_excluded("email_draft"):
        registry.register(email_draft)
    if not _desktop_excluded("design_extract"):
        registry.register(design_extract)

    if not _desktop_excluded("web_fetch"):
        registry.register(web_fetch)
    if not _desktop_excluded("web_search"):
        registry.register(web_search)

    if not _desktop_excluded("browser_open"):
        registry.register(browser_open)
    if not _desktop_excluded("browser_snapshot"):
        registry.register(browser_snapshot)
    if not _desktop_excluded("browser_click"):
        registry.register(browser_click)
    if not _desktop_excluded("browser_fill"):
        registry.register(browser_fill)
    if not _desktop_excluded("browser_screenshot"):
        registry.register(browser_screenshot)
    if not _desktop_excluded("browser_eval"):
        registry.register(browser_eval)

    if not _desktop_excluded("app_create"):
        registry.register(app_create)
    if not _desktop_excluded("app_list"):
        registry.register(app_list)
    if not _desktop_excluded("app_schema"):
        registry.register(app_schema)
    if not _desktop_excluded("app_delete"):
        registry.register(app_delete)
    if not _desktop_excluded("app_insert"):
        registry.register(app_insert)
    if not _desktop_excluded("app_update"):
        registry.register(app_update)
    if not _desktop_excluded("app_delete_row"):
        registry.register(app_delete_row)
    if not _desktop_excluded("app_column_add"):
        registry.register(app_column_add)
    if not _desktop_excluded("app_column_delete"):
        registry.register(app_column_delete)
    if not _desktop_excluded("app_column_rename"):
        registry.register(app_column_rename)
    if not _desktop_excluded("app_query"):
        registry.register(app_query)
    if not _desktop_excluded("app_search_fts"):
        registry.register(app_search_fts)
    if not _desktop_excluded("app_import_csv"):
        registry.register(app_import_csv)
    if not _desktop_excluded("app_summarize"):
        registry.register(app_summarize)

    if not _desktop_excluded("subagent_create"):
        registry.register(subagent_create)
    if not _desktop_excluded("subagent_delegate"):
        registry.register(subagent_delegate)
    if not _desktop_excluded("subagent_start"):
        registry.register(subagent_start)
    if not _desktop_excluded("subagent_check"):
        registry.register(subagent_check)
    if not _desktop_excluded("subagent_tasks"):
        registry.register(subagent_tasks)
    if not _desktop_excluded("subagent_list"):
        registry.register(subagent_list)
    if not _desktop_excluded("subagent_instruct"):
        registry.register(subagent_instruct)
    if not _desktop_excluded("subagent_cancel"):
        registry.register(subagent_cancel)
    if not _desktop_excluded("subagent_delete"):
        registry.register(subagent_delete)
    if not _desktop_excluded("subagent_update"):
        registry.register(subagent_update)
    if not _desktop_excluded("summarize_session"):
        registry.register(summarize_session)

    if not _desktop_excluded("mcp_list"):
        registry.register(mcp_list)
    if not _desktop_excluded("mcp_reload"):
        registry.register(mcp_reload)
    if not _desktop_excluded("mcp_tools"):
        registry.register(mcp_tools)

    if not _desktop_excluded("skills_load"):
        registry.register(skills_load)
    if not _desktop_excluded("skills_reload"):
        registry.register(skills_reload)

    if not _desktop_excluded("research_start"):
        registry.register(research_start)
    if not _desktop_excluded("research_list"):
        registry.register(research_list)


_register_all()


def get_native_tools() -> list[ToolDefinition]:
    """Return all registered SDK-native ToolDefinitions (lazy registration)."""
    if not _registry.list_tools():
        _register_all()
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
