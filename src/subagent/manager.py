"""Subagent manager — SDK-native implementation built on the AgentLoop."""

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import yaml

from src.app_logging import get_logger
from src.config import get_settings
from src.skills import get_skill_registry
from src.storage.paths import get_paths
from src.subagent.config import SubagentConfig
from src.subagent.validation import validate_subagent_config

logger = get_logger()


def _get_sdk_tools_for_subagent(config: SubagentConfig) -> list[Any]:
    """Get SDK ToolDefinition list for a subagent."""
    from src.sdk.native_tools import get_native_tools

    all_native = get_native_tools()

    if not config.tools:
        return list(all_native)

    tool_map = {t.name: t for t in all_native}
    return [tool_map[name] for name in config.tools if name in tool_map]


class SubagentManager:
    """Manages subagent creation, invocation, and tracking."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.settings = get_settings()
        self.base_path = get_paths(user_id).user_subagents_dir()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = {}

    def create(
        self,
        name: str,
        model: str | None = None,
        description: str = "",
        skills: list[str] | None = None,
        tools: list[str] | None = None,
        system_prompt: str | None = None,
        mcp_config: dict[str, Any] | None = None,
    ) -> tuple[Any | None, dict[str, Any]]:
        """Create a subagent.

        Returns:
            Tuple of (loop_config, validation_result_dict)
            If validation fails, loop_config is None and result contains errors
        """
        config_dict = {
            "name": name,
            "model": model,
            "description": description,
            "skills": skills or [],
            "tools": tools or [],
            "system_prompt": system_prompt,
        }

        subagent_path = self.base_path / name
        validation = validate_subagent_config(self.user_id, config_dict, subagent_path)

        if not validation.valid:
            logger.warning(
                "subagent.validation.failed",
                {"name": name, "errors": validation.errors},
                user_id=self.user_id,
            )
            if subagent_path.exists():
                import shutil

                shutil.rmtree(subagent_path, ignore_errors=True)
            return None, {
                "valid": validation.valid,
                "errors": validation.errors,
                "warnings": validation.warnings,
            }

        subagent_path.mkdir(parents=True, exist_ok=True)

        config_dict_clean = {k: v for k, v in config_dict.items() if v is not None}

        if system_prompt is None:
            system_prompt = f"""You are {name}, a specialized subagent.

{description or "You are helpful and autonomous."}

You have access to tools and skills as configured.
"""
        config_dict_clean["system_prompt"] = system_prompt

        config_path = subagent_path / "config.yaml"
        config_path.write_text(yaml.dump(config_dict_clean))

        if mcp_config is not None:
            (subagent_path / ".mcp.json").write_text(json.dumps(mcp_config, indent=2))

        full_config = self._load_config(name)

        system_prompt = self._build_system_prompt(full_config)

        loop_config = {
            "model": full_config.model or self.settings.agent.model,
            "system_prompt": system_prompt,
            "tools": full_config.tools,
            "name": name,
        }

        self._cache[name] = loop_config

        logger.info(
            "subagent.created",
            {"name": name, "model": full_config.model, "skills": full_config.skills},
            user_id=self.user_id,
        )

        return loop_config, {
            "valid": True,
            "errors": [],
            "warnings": validation.warnings,
            "message": f"Subagent '{name}' created successfully",
        }

    def invoke(self, name: str, task: str) -> dict[str, Any]:
        """Invoke a subagent to execute a task.

        Args:
            name: Subagent name
            task: Task description

        Returns:
            Result dict with output and metadata
        """
        config = self._get(name)
        if not config:
            return {
                "success": False,
                "error": f"Subagent '{name}' not found. Create it first with subagent_create.",
            }

        try:
            import asyncio

            result = asyncio.run(self._invoke_async(config, task))
        except RuntimeError:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self._invoke_async(config, task))
                    try:
                        result = future.result(timeout=120)
                    except Exception as e:
                        return {"success": False, "error": str(e)}
            else:
                result = asyncio.run(self._invoke_async(config, task))

        logger.info(
            "subagent.invoked",
            {"name": name, "task": task[:100]},
            user_id=self.user_id,
        )

        return result

    async def _invoke_async(self, config: dict[str, Any], task: str) -> dict[str, Any]:
        """Run subagent with SDK AgentLoop."""
        from src.sdk.loop import AgentLoop
        from src.sdk.messages import Message
        from src.sdk.providers.factory import create_model_from_config

        model_str = config.get("model", self.settings.agent.model)
        provider = create_model_from_config(model_str)

        tool_names = config.get("tools", [])
        from src.sdk.native_tools import get_native_tools

        all_native = get_native_tools()
        if tool_names:
            tool_map = {t.name: t for t in all_native}
            sdk_tools = [tool_map[n] for n in tool_names if n in tool_map]
        else:
            sdk_tools = list(all_native)

        # Roadmap P0-T3 follow-up: loops built directly (bypassing
        # create_sdk_loop) must still wire the per-user audit store.
        from src.sdk.audit import ensure_audit_store_subscribed

        ensure_audit_store_subscribed(self.user_id)

        loop = AgentLoop(
            provider=provider,
            tools=sdk_tools,
            system_prompt=config.get("system_prompt", ""),
        )

        messages = [Message.user(task)]
        state = await loop.run(messages)

        output = ""
        state_messages: list[Message] = state.messages  # type: ignore[attr-defined]
        if state_messages:
            for msg in reversed(state_messages):
                if msg.role == "assistant" and msg.content:
                    content = msg.content
                    if isinstance(content, str) and content.strip():
                        output = content
                        break
                    elif isinstance(content, list):
                        text_parts = [
                            b.get("text", "")
                            for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        combined = "".join(text_parts).strip()
                        if combined:
                            output = combined
                            break

        if not output:
            output = "Subagent completed with no text output."

        return {
            "success": True,
            "output": output,
            "name": config.get("name", "unknown"),
            "task": task,
        }

    def list_all(self) -> list[dict[str, Any]]:
        """List all subagents for the user."""
        if not self.base_path.exists():
            return []

        subagents = []
        for subagent_dir in self.base_path.iterdir():
            if subagent_dir.is_dir():
                config = self._load_config(subagent_dir.name)
                subagents.append(
                    {
                        "name": config.name,
                        "model": config.model,
                        "description": config.description,
                        "skills": config.skills,
                        "tools": config.tools,
                    }
                )

        return subagents

    def get_progress(self, task_name: str) -> dict[str, Any]:
        """Get subagent progress from planning files."""
        base = get_paths(self.user_id).workspace_files_dir() / "planning" / task_name

        result: dict[str, Any] = {
            "task_plan": None,
            "progress": None,
            "findings": None,
            "exists": base.exists(),
        }

        if not base.exists():
            return result

        for key in ["task_plan", "progress", "findings"]:
            file_path = base / f"{key}.md"
            if file_path.exists():
                result[key] = file_path.read_text()

        return result

    def invoke_batch(self, tasks: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Invoke multiple subagents in parallel."""
        import concurrent.futures

        results = []

        def run_single(task_dict: dict[str, str]) -> dict[str, Any]:
            name = task_dict.get("name", "")
            task = task_dict.get("task", "")
            result = self.invoke(name, task)
            result["name"] = name
            return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = [executor.submit(run_single, task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

        name_to_result = {r["name"]: r for r in results}
        ordered_results = [name_to_result[t["name"]] for t in tasks]

        logger.info(
            "subagent.batch_invoked",
            {"count": len(tasks), "user_id": self.user_id},
            user_id=self.user_id,
        )

        return ordered_results

    def _load_config(self, name: str) -> SubagentConfig:
        """Load subagent config from file."""
        config_path = self.base_path / name / "config.yaml"

        if not config_path.exists():
            return SubagentConfig(name=name)

        config_dict = yaml.safe_load(config_path.read_text()) or {}
        config_dict.pop("name", None)
        return SubagentConfig(name=name, **config_dict)

    def _build_system_prompt(self, config: SubagentConfig) -> str:
        """Build system prompt with skills injected."""
        registry = get_skill_registry(user_id=self.user_id)

        skills_content = ""
        for skill_name in config.skills:
            skill = registry.get_skill(skill_name)
            if skill:
                skills_content += f"\n\n## {skill_name}\n{skill['content']}"

        custom_prompt = config.system_prompt + "\n\n" if config.system_prompt else ""

        system_prompt = f"{custom_prompt}{skills_content}"
        return system_prompt

    def _load_mcp_tools(self, subagent_path: Path) -> list[Any]:
        """Load MCP tools from subagent's .mcp.json if exists (sync wrapper)."""
        mcp_path = subagent_path / ".mcp.json"
        if not mcp_path.exists():
            return []

        try:
            mcp_config = json.loads(mcp_path.read_text())
            if not mcp_config:
                return []

            from src.sdk.tools_core.mcp_manager import get_mcp_manager

            mcp_manager = get_mcp_manager(self.user_id)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, mcp_manager.get_tools())
                    all_tools = future.result(timeout=30)
            else:
                all_tools = asyncio.run(mcp_manager.get_tools())

            allowed_servers = set(mcp_config.keys())
            filtered_tools = [
                tool
                for tool in all_tools
                if hasattr(tool, "name")
                and any(
                    tool.name == server or tool.name.startswith(server + "_")
                    for server in allowed_servers
                )
            ]

            generic_tools = [
                tool
                for tool in all_tools
                if not any(
                    tool.name == server or tool.name.startswith(server + "_")
                    for server in allowed_servers
                )
            ]

            return filtered_tools + generic_tools

        except Exception as e:
            logger.warning(
                "subagent.mcp.load_error",
                {"path": str(mcp_path), "error": str(e), "error_type": type(e).__name__},
                user_id=self.user_id,
            )
            return []

    def invalidate_cache(self, name: str | None = None) -> None:
        """Invalidate subagent cache. If name is None, clear all cache."""
        if not hasattr(self, "_cache"):
            return
        if name is None:
            self._cache.clear()
        elif name in self._cache:
            del self._cache[name]

    def _get(self, name: str) -> dict[str, Any] | None:
        """Get subagent config from cache, reloading if config changed."""
        subagent_path = self.base_path / name
        if not subagent_path.exists():
            self.invalidate_cache(name)
            return None

        config_path = subagent_path / "config.yaml"
        cached_config = self._cache.get(name)
        if cached_config is not None and config_path.exists():
            config_mtime = config_path.stat().st_mtime
            cache_key = f"_mtime_{name}"
            cached_mtime = getattr(self, cache_key, 0)
            if config_mtime == cached_mtime:
                return cast(dict[str, Any], cached_config)

        config = self._load_config(name)
        system_prompt = self._build_system_prompt(config)

        loop_config = {
            "model": config.model or self.settings.agent.model,
            "system_prompt": system_prompt,
            "tools": config.tools,
            "name": name,
        }

        self._cache[name] = loop_config
        if config_path.exists():
            setattr(self, f"_mtime_{name}", config_path.stat().st_mtime)

        return loop_config


_managers: dict[str, SubagentManager] = {}


def get_subagent_manager(user_id: str) -> SubagentManager:
    """Get or create subagent manager for user."""
    if user_id not in _managers:
        _managers[user_id] = SubagentManager(user_id)
    return _managers[user_id]
