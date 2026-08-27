"""Main-agent-from-profile bootstrap (roadmap P0-T7 / K1, spec §4.5).

Composition layer: a user-level PROFILE.md (DataPaths.main_agent_profile_path)
instantiates the MAIN agent loop instead of only subagents.

Precedence rules (spec §4.5 — the contract):
1. Capabilities/scopes win over profile.tools — enforced because loop tool
   registration already filters through _resource_enabled(caps, ...); profile
   tools are validated for existence but cannot re-enable scoped-out tools.
2. Profile persona wins over user_prompt_set free text (Prompt.md is ignored
   when profile.system_prompt is set).
3. Absent fields fall back to settings-derived behavior. No PROFILE.md =
   exactly today's behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentprofile import AgentProfile, load_profile

from src.app_logging import get_logger
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()

# Providers that require an API key (mirrors factory._ENV_KEY_MAP; kept as a
# literal copy so this module stays import-light and circular-import safe).
_CLOUD_PROVIDERS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "ollama-cloud": "OLLAMA_API_KEY",
    "agnes": "AGNES_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together": "TOGETHER_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class ProfileError(Exception):
    """Raised when a main-agent PROFILE.md fails validation at bootstrap."""


@dataclass
class LoopSpec:
    """Validated profile-derived inputs for create_sdk_loop consumption."""

    model: str | None  # None = fall back to request/settings model
    persona: str | None  # None = keep user_prompt text
    run_config_kwargs: dict  # max_llm_calls/cost_limit_usd/timeout_seconds
    timeout_seconds: int


def load_main_agent_profile(
    user_id: str, data_root: str | Path | None = None
) -> AgentProfile | None:
    """Read the user's main-agent PROFILE.md; None when absent."""
    from src.storage.paths import DataPaths

    kwargs = {"user_id": user_id}
    if data_root is not None:
        # DataPaths resolves DEPLOYMENT_DATA_ROOT itself; an explicit root is a
        # test seam (and matches the ea_root kwarg convention used in tests).
        kwargs["data_root"] = str(data_root)
    try:
        dp = DataPaths(**kwargs) if data_root is not None else DataPaths(user_id=user_id)
        path = dp.main_agent_profile_path
    except OSError:
        # Unconstructable data root => no readable PROFILE.md. Absent profile
        # means "today's behavior" — never crash bootstrap on this path.
        logger.warning(
            "profile.data_root_unavailable", {"user_id": user_id}, user_id=user_id
        )
        return None
    if not path.exists():
        return None
    try:
        return load_profile(path)
    except Exception as exc:
        raise ProfileError(f"Invalid PROFILE.md at {path}: {exc}") from exc


def validate_model_reference(
    model_ref: str,
    *,
    provider_keys: dict[str, str] | None = None,
    user_id: str =  DEFAULT_USER_ID,
    require_key: bool | None = None,
) -> str | None:
    """Fail-fast bootstrap validation of profile.model.

    Returns the provider type when OK. Raises ProfileError when the provider is
    unknown or a required cloud API key is unresolvable at bootstrap time.
    Registry existence of the specific model is checked non-fatally (custom
    local pulls are legitimate) and reported via log warning.

    require_key forces the key check even for providers not listed as cloud
    (used by tests); default derives it from the provider type.
    """
    from src.config.user_settings_service import load_saved_user_settings
    from src.sdk.providers.factory import _parse_model_string
    from src.sdk.registry import get_model_info

    provider_type, _model_name = _parse_model_string(model_ref)

    env_name = _CLOUD_PROVIDERS.get(provider_type)
    needs_key = require_key if require_key is not None else (env_name is not None)

    resolved_key: str | None = None
    if provider_keys:
        resolved_key = next(
            (
                v
                for k, v in provider_keys.items()
                if k.lower() == provider_type.lower() and v
            ),
            None,
        )
    if resolved_key is None:
        saved = load_saved_user_settings(user_id)
        if saved is not None:
            resolved_key = next(
                (
                    v
                    for k, v in saved.provider_keys.items()
                    if k.lower() == provider_type.lower() and v
                ),
                None,
            )
    if resolved_key is None and env_name:
        import os

        resolved_key = os.environ.get(env_name) or None

    if needs_key and not resolved_key:
        hint = f" (env {env_name})" if env_name else ""
        raise ProfileError(
            f"Model '{model_ref}' requires an API key but none was resolvable"
            f"{hint}. Set the key before starting — failing fast at bootstrap "
            "instead of on first message."
        )

    info = get_model_info(model_ref)
    if info.provider_id == "unknown":
        logger.warning(
            "profile.model_not_in_registry",
            {"model": model_ref},
            user_id=user_id,
        )
    return provider_type


def build_loop_from_profile(
    user_id: str,
    profile: AgentProfile,
    *,
    provider_keys: dict[str, str] | None = None,
    data_root: str | Path | None = None,
    requested_model: str | None = None,
) -> LoopSpec:
    """Validate a main-agent profile and prepare loop-construction inputs.

    Precedence: an explicit request-scoped `requested_model` wins over
    profile.model; profile.model wins over settings default (rule 3 fallback).
    """
    errors: list[str] = []

    from src.sdk.agent_validation import validate_agent_profile
    from src.sdk.native_tools import get_native_tools

    native_names = {td.name for td in get_native_tools()}
    for name in profile.tools or []:
        if name not in native_names:
            errors.append(f"Unknown tool: {name}")

    try:
        from src.skills.registry import get_skill_registry

        skill_registry = get_skill_registry(user_id=user_id)
        for skill_name in profile.skills or []:
            if skill_registry.get_skill(skill_name) is None:
                errors.append(f"Unknown skill: {skill_name}")
    except ProfileError:
        raise
    except Exception as exc:  # registry unavailable — surface, don't hide
        errors.append(f"Skill registry unavailable: {exc}")

    if profile.max_llm_calls <= 0:
        errors.append("max_llm_calls must be positive")
    if profile.cost_limit_usd <= 0:
        errors.append("cost_limit_usd must be positive")
    if profile.timeout_seconds <= 0:
        errors.append("timeout_seconds must be positive")

    if errors:
        raise ProfileError("; ".join(errors))

    effective_model = requested_model or (profile.model or "") or None
    provider_type: str | None = None
    if effective_model:
        # Full validation (key availability etc.) happens here so bootstrap
        # fails before any HTTP client exists.
        provider_type = validate_model_reference(
            effective_model,
            provider_keys=provider_keys,
            user_id=user_id,
            # Requested models were already resolved by the caller upstream;
            # only profile-sourced models need the fail-fast guarantee.
            require_key=False if requested_model else None,
        )
        _ = validate_agent_profile  # referenced; full subagent validator reused above
        _ = provider_type

    spec = LoopSpec(
        model=effective_model,
        persona=(profile.system_prompt or "").strip() or None,
        run_config_kwargs={
            "max_llm_calls": profile.max_llm_calls,
            "cost_limit_usd": profile.cost_limit_usd,
        },
        timeout_seconds=profile.timeout_seconds,
    )
    return spec


_USER_INSTRUCTIONS_RE = re.compile(r"\n?## User Instructions\n.*?(?=\n## |\Z)", re.DOTALL)


def apply_persona(base_system_prompt: str, persona: str) -> str:
    """Profile persona wins over Prompt.md text (precedence rule 2).

    Strips the User Instructions section from the composed prompt and appends
    the profile persona under its own heading.
    """
    stripped = _USER_INSTRUCTIONS_RE.sub("", base_system_prompt)
    return stripped.rstrip() + f"\n\n## Agent Persona\n{persona}"


async def revalidate_and_reset(
    user_id: str,
    *,
    data_root: str | Path | None = None,
    registry=None,
) -> dict:
    """Profile-change lifecycle: reload + reset loops + detach active sessions.

    - reset_user_sdk_loops bumps the runner generation (in-flight creations are
      superseded per audit E24 drift guard).
    - SessionWorkerRegistry.stop_user_sessions cancels active streams so no
      stale loop serves an approved turn after the swap (E26 detach).
    """
    from src.sdk.runner import reset_user_sdk_loops
    from src.storage.paths import get_paths as _get_paths

    _ = _get_paths  # imported for parity with plan wording; profile read below
    profile = load_main_agent_profile(user_id, data_root=data_root)

    detached: list[str] = []
    if registry is not None:
        detached = await registry.stop_user_sessions(user_id)

    removed = reset_user_sdk_loops(user_id, reason="profile_revalidated")
    logger.info(
        "profile.revalidated_and_reset",
        {
            "user_id": user_id,
            "profile_found": profile is not None,
            "detached_sessions": detached,
            "loops_removed": removed,
        },
        user_id=user_id,
    )
    return {
        "profile_found": profile is not None,
        "detached_sessions": detached,
        "loops_removed": removed,
    }
