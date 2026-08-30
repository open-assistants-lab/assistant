"""Pure resolution of persisted and host-provided user settings."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from src.config.user_settings import (
    CanonicalModel,
    EffectiveUserSettings,
    EffectiveVerificationSettings,
    GraderPromptResponse,
    ProviderStatus,
    SavedUserSettings,
    SavedUserSettingsView,
    UserSettingsResponse,
    canonical_model,
)
from src.sdk.run_models import RubricAvailability, RubricUnavailableReason

_KNOWN_PROVIDER_ENV: Mapping[str, tuple[str, ...]] = {
    "agnes": ("AGNES_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "gemini": ("GOOGLE_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "ollama": ("OLLAMA_API_KEY",),
    "ollama-cloud": ("OLLAMA_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
}
_LOCAL_PROVIDERS = frozenset({"ollama", "llamacpp"})


class SettingsResolutionError(Exception):
    """Raised when host settings cannot produce a valid effective configuration."""


def resolve_provider_statuses(
    saved: SavedUserSettings,
    providers: Sequence[Mapping[str, object]],
    environ: Mapping[str, str],
) -> Mapping[str, ProviderStatus]:
    """Resolve provider credential metadata without exposing credential values."""
    statuses: dict[str, ProviderStatus] = {}
    for descriptor in providers:
        if not isinstance(descriptor, Mapping):
            continue
        raw_id = descriptor.get("id")
        raw_name = descriptor.get("name")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        provider_id = raw_id.strip()
        if provider_id in statuses:
            continue

        raw_env = descriptor.get("env", [])
        if not isinstance(raw_env, (list, tuple)) or any(
            not isinstance(name, str) or not name.strip() for name in raw_env
        ):
            continue
        env_names = (
            *_KNOWN_PROVIDER_ENV.get(provider_id, ()),
            *(name.strip() for name in raw_env),
        )

        if provider_id in saved.provider_keys:
            key_source = "user"
        elif any(environ.get(name, "").strip() for name in env_names):
            key_source = "hosted" if provider_id == "agnes" else "env"
        elif provider_id in _LOCAL_PROVIDERS:
            key_source = "local"
        else:
            key_source = "none"

        statuses[provider_id] = ProviderStatus(
            name=raw_name,
            has_key=key_source != "none",
            key_configured_via_env=key_source == "env",
            key_source=key_source,
        )
    return MappingProxyType(statuses)


def load_saved_user_settings(user_id: str) -> SavedUserSettings | None:
    """Load a user's saved settings, or None when unavailable.

    Shared by the runtime paths (grader loader, title generation,
    summarization) so user-configured models take effect without the
    settings router being involved.
    """
    try:
        from src.config.user_settings_store import UserSettingsStore

        return UserSettingsStore(user_id).load()
    except Exception:
        return None


def resolve_effective_user_settings(
    *,
    saved: SavedUserSettings,
    prompt: GraderPromptResponse | None,
    host_default_model: str,
    host_title_model: str | None = None,
    host_summarization_model: str | None = None,
    host_verification_enabled: bool,
    host_grader_model: str | None,
    host_max_attempts: int,
    provider_status: Mapping[str, ProviderStatus],
    model_available: Callable[[str], bool],
    provider_available: Callable[[str], bool],
) -> EffectiveUserSettings:
    """Resolve persisted overrides against validated host defaults and availability."""
    try:
        default_model = saved.default_model or (
            canonical_model(host_default_model) if host_default_model.strip() else None
        )
    except (TypeError, ValueError):
        raise SettingsResolutionError("Invalid host default model configuration") from None

    def _resolve_role_model(
        saved_value: str | None, host_value: str | None
    ) -> CanonicalModel | None:
        # Catalog drift: a saved/host model that models.dev removed or
        # renamed must not 404 at runtime. Walk the chain (saved → host →
        # default) to the first model the catalog knows; the static seeds
        # guarantee the default is always catalogued, so the terminal is
        # effectively always valid.
        for candidate in (saved_value, host_value, default_model):
            if not candidate:
                continue
            resolved = canonical_model(candidate)
            if resolved is not None and model_available(resolved):
                return resolved
        return default_model

    title_model = _resolve_role_model(saved.title_model, host_title_model)
    summarization_model = _resolve_role_model(saved.summarization_model, host_summarization_model)

    enabled = (
        saved.verification.enabled
        if saved.verification.enabled is not None
        else host_verification_enabled
    )
    grader_candidate = saved.verification.grader_model
    if grader_candidate is None:
        grader_candidate = (
            host_grader_model
            if isinstance(host_grader_model, str) and host_grader_model.strip()
            else default_model
        )
    try:
        grader_model = canonical_model(grader_candidate)
    except (TypeError, ValueError):
        grader_model = None

    max_attempts = saved.verification.max_attempts
    if max_attempts is None:
        if (
            not isinstance(host_max_attempts, int)
            or isinstance(host_max_attempts, bool)
            or not 1 <= host_max_attempts <= 3
        ):
            raise SettingsResolutionError("Invalid host verification max attempts configuration")
        max_attempts = host_max_attempts

    prompt_available = prompt is not None and bool(prompt.content.strip())
    prompt_hash = prompt.content_hash if prompt is not None and prompt_available else None
    if not enabled:
        verification = EffectiveVerificationSettings(
            state=RubricAvailability.OFF,
            grader_model=grader_model,
            max_attempts=max_attempts,
            grader_prompt_hash=prompt_hash,
        )
    else:
        reason: RubricUnavailableReason | None = None
        if not prompt_available:
            reason = RubricUnavailableReason.MISSING_PROMPT
        elif grader_model is None:
            reason = RubricUnavailableReason.INVALID_GRADER_MODEL
        else:
            provider_id = grader_model.partition(":")[0]
            if not provider_available(provider_id):
                reason = RubricUnavailableReason.PROVIDER_UNAVAILABLE
            elif not model_available(grader_model):
                reason = RubricUnavailableReason.INVALID_GRADER_MODEL
            elif provider_id not in _LOCAL_PROVIDERS and not provider_status.get(
                provider_id,
                ProviderStatus(name=provider_id, has_key=False, key_source="none"),
            ).has_key:
                reason = RubricUnavailableReason.MISSING_CREDENTIALS

        verification = EffectiveVerificationSettings(
            state=(RubricAvailability.UNAVAILABLE if reason else RubricAvailability.ON),
            unavailable_reason=reason,
            grader_model=grader_model,
            max_attempts=max_attempts,
            grader_prompt_hash=prompt_hash,
        )

    return EffectiveUserSettings(
        default_model=default_model,
        title_model=title_model,
        summarization_model=summarization_model,
        verification=verification,
    )


def build_user_settings_response(
    saved: SavedUserSettings,
    effective: EffectiveUserSettings,
    provider_status: Mapping[str, ProviderStatus],
) -> UserSettingsResponse:
    """Build the secret-free public settings response."""
    return UserSettingsResponse(
        revision=saved.revision,
        saved=SavedUserSettingsView(
            default_model=saved.default_model,
            title_model=saved.title_model,
            summarization_model=saved.summarization_model,
            verification=saved.verification,
        ),
        effective=effective,
        provider_status=provider_status,
    )
