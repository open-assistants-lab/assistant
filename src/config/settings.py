"""Settings module for Assistant."""

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root — resolved from THIS file so config/.env are found
# regardless of process CWD (audit E23).
REPO_ROOT = Path(__file__).resolve().parents[2]


class _BaseSettings(BaseSettings):
    """Base settings with common config."""

    model_config = SettingsConfigDict(extra="ignore")


class DeploymentConfig(_BaseSettings):
    """Deployment configuration.

    solo: Single user on desktop (.dmg/.exe)
    multi-user: Docker container per user, org gets many containers
    """

    mode: str = Field(default="solo")
    data_path: str = Field(default="data")
    data_root: str = Field(
        default="",
        description="Root for user data directory. Empty string means Path.home() / 'Assistant'.",
    )

    model_config = SettingsConfigDict(env_prefix="DEPLOYMENT_")


class AgentConfig(_BaseSettings):
    """Agent configuration."""

    name: str = Field(default="Assistant")
    # No provider is baked in as the shipped default: set agent.model in
    # config.yaml (deployment-level default) or per-user PROFILE.md (primary
    # agent configuration). Empty -> fail-fast at first use with guidance.
    model: str = Field(
        default="",
        description="Default model as 'provider:model' (e.g. anthropic:claude-...). "
        "Empty requires PROFILE.md or per-request model.",
    )
    title_model: str = Field(
        default="", description="Model for chat title summarization (empty = use model)"
    )
    system_prompt: str = Field(default="You are a helpful assistant.")
    pool_size: int = Field(default=3)

    model_config = SettingsConfigDict(env_prefix="AGENT_")


class MessagesConfig(_BaseSettings):
    """Messages (long-term) configuration using SQLite + FTS5 + ChromaDB."""

    enabled: bool = True
    max_chroma_index_gb: int = Field(
        default=5,
        description=(
            "Maximum size in GB for a single ChromaDB HNSW index file (link_lists.bin). "
            "When exceeded at startup, the index is automatically rebuilt. "
            "Set to 0 to disable health checks."
        ),
    )

    model_config = SettingsConfigDict(env_prefix="MESSAGES_")


class StoreConfig(_BaseSettings):
    """Store configuration for long-term memory."""

    enabled: bool = True

    model_config = SettingsConfigDict(env_prefix="STORE_")


class SummarizationConfig(_BaseSettings):
    """Summarization middleware configuration (short-term token reduction)."""

    enabled: bool = True
    # Empty = use agent.model (never a provider-specific fallback).
    model: str = Field(default="")
    trigger: list[Any] = Field(default_factory=lambda: ["tokens", 50000])
    keep: list[Any] = Field(default_factory=lambda: ["messages", 20])
    trim_tokens_to_summarize: int | None = 4000
    prompt_file: str = Field(default="summarisation_prompt.md", description="Filename for summary prompt — seeded per user from seeds/prompts/")

    # Old fields for backward compat
    trigger_tokens: int | None = None
    keep_tokens: int | None = None

    model_config = SettingsConfigDict(env_prefix="SUMMARY_")

    def get_trigger(self) -> Any:
        if self.trigger_tokens is not None:
            return ("tokens", self.trigger_tokens)
        return tuple(self.trigger) if self.trigger else None

    def get_keep(self) -> Any:
        if self.keep_tokens is not None:
            return ("tokens", self.keep_tokens)
        return tuple(self.keep) if self.keep else ("messages", 20)


class GovernanceConfig(_BaseSettings):
    """Durable approval-gated tools (M4, issue #6)."""

    enabled: bool = False
    # tool name -> tier: autonomous | show_then_auto_send | explicit | hard_block
    tiers: dict[str, str] = Field(default_factory=dict)
    auto_send_expiry_seconds: int = 300

    model_config = SettingsConfigDict(env_prefix="GOVERNANCE_")


class VerificationConfig(_BaseSettings):
    """Verification (rubric middleware) configuration."""

    enabled: bool = False
    default_rubric: str = ""
    grader_model: str = Field(default="", description="Model for grading (empty = use agent model)")
    grader_system_prompt: str = ""
    grader_tools: list[str] = Field(default_factory=list, description="Tool names the grader may call")
    max_iterations: int = 3
    # Selective verification (C11): "off" (never verify unless requested),
    # "on" (always verify when configured), "auto" (skip the grader for
    # trivial turns via a deterministic post-run decision).
    mode: str = "off"
    # auto-skip thresholds: skip only if response shorter than this AND
    # history smaller than verify_min_history_tokens AND response under
    # verify_min_response_chars; verify when any threshold is exceeded.
    skip_max_response_chars: int = 200
    verify_min_history_tokens: int = 4000
    verify_min_response_chars: int = 800
    # Always verify when any keyword appears in the prompt or response.
    risk_keywords: list[str] = Field(
        default_factory=lambda: [
            "password",
            "api key",
            "secret",
            "credential",
            "token",
            "financial",
            "payment",
            "bank",
            "medical",
            "health",
            "delete",
            "remove file",
            "drop table",
            "sudo",
            "rm -rf",
        ]
    )

    model_config = SettingsConfigDict(env_prefix="VERIFICATION_")


class HillClimbingConfig(_BaseSettings):
    """Hill-climbing (loop 4) configuration."""

    mode: str = "human_review"  # "human_review" | "auto_apply"
    auto_apply_risk_threshold: str = "low"  # "low" | "medium" | "high"
    analysis_model: str = Field(default="", description="Model for analysis LLM (empty = use agent model)")
    eval_enabled: bool = True

    model_config = SettingsConfigDict(env_prefix="HILL_CLIMBING_")


class MemoryConfig(_BaseSettings):
    """Memory configuration."""

    messages: MessagesConfig = Field(default_factory=MessagesConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)
    consolidate_after_messages: int = 10  # 0=disabled, N=consolidate after N messages


class LangfuseConfig(_BaseSettings):
    """Langfuse observability configuration."""

    enabled: bool = False
    public_key: str = ""
    secret_key: str = ""
    host: str = "https://cloud.langfuse.com"
    environment: str = ""  # production, development, staging

    model_config = SettingsConfigDict(env_prefix="LANGFUSE_")


class LoggingConfig(_BaseSettings):
    """Logging configuration."""

    enabled: bool = True
    level: str = "info"  # debug, info, warning, error
    json_dir: str = ""

    model_config = SettingsConfigDict(env_prefix="LOGGING_")


class ObservabilityConfig(_BaseSettings):
    """Observability configuration."""

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)


class AuthConfig(_BaseSettings):
    """API key authentication for remote connections.

    Solo (localhost): auth disabled if api_key is empty. Localhost bypass enabled by default.
    Multi-device WAN: set API_KEY to require auth on non-localhost connections.
    Multi-tenant: each container has its own API_KEY.
    """

    api_key: str = Field(default="")
    solo_bypass: bool = Field(default=True)
    # Phase 2 M2.1: per-user generated keys -> identities. When off (default)
    # the SharedSecretResolver path is unchanged; when on, Bearer keys from
    # data/auth.db map to per-user identities (untrusted domain).
    per_user_auth: bool = Field(default=False)
    # Trusted CORS origins, comma-separated (audit B17). Empty -> wildcard
    # origins WITHOUT credentials (safe default for local dev).
    cors_origins: str = Field(
        default="", description="Comma-separated trusted CORS origins"
    )

    model_config = SettingsConfigDict(env_prefix="")


class ApiConfig(_BaseSettings):
    """API configuration."""

    host: str = "0.0.0.0"
    # 8080 = the native-app (Zig) client contract when no config.yaml exists;
    # docker overrides via API_PORT env (env beats yaml for api.*).
    port: int = 8080
    # Public URL used for OAuth redirect_uri callbacks (e.g. the browser must
    # be able to reach this). Defaults to localhost:port for local dev.
    public_url: str = ""

    model_config = SettingsConfigDict(env_prefix="API_")


class CliConfig(_BaseSettings):
    """CLI configuration."""

    model_config = SettingsConfigDict(env_prefix="CLI_")


class ToolsConfig(_BaseSettings):
    """Tools configuration."""

    firecrawl_api_key: str = Field(default="", validation_alias="FIRECRAWL_API_KEY")
    firecrawl_base_url: str = Field(default="", validation_alias="FIRECRAWL_BASE_URL")
    max_retries: int = 3
    timeout: int = 30

    model_config = SettingsConfigDict(env_prefix="TOOLS_")


class SkillsConfig(_BaseSettings):
    """Skills configuration."""

    model_config = SettingsConfigDict(env_prefix="SKILLS_")


class FilesystemConfig(_BaseSettings):
    """Filesystem tools configuration."""

    enabled: bool = True
    max_file_size_mb: int = 10
    workspace_root: str | None = Field(
        default=None,
        description="Shared workspace directory. When set, all filesystem tools "
        "resolve relative paths from this directory instead of per-user workspace. "
        "Example: /Users/eddy/shared_workspace",
    )

    model_config = SettingsConfigDict(env_prefix="FILESYSTEM_")


class EmailConfig(_BaseSettings):
    """Email configuration for Gmail/Outlook via the GmailClient OAuth path.

    Gmail OAuth client credentials are entered via the ConnectKit connect form
    (gmail.yaml required_fields client_id/client_secret) and stored in the
    vault. EMAIL_GWS_CLIENT_ID / EMAIL_GWS_CLIENT_SECRET are retained for
    backward compatibility (legacy gws config) but are NOT consumed by the
    GmailClient path — a deployment that sets only these env vars and skips
    the connect form will have empty OAuth client creds (roadmap G4).
    """

    enabled: bool = True
    gws_client_id: str = Field(default="")
    gws_client_secret: str = Field(default="")
    m365_client_id: str = Field(default="")
    sync_interval_minutes: int = Field(default=15)

    model_config = SettingsConfigDict(env_prefix="EMAIL_")


class ConnectKitConfig(_BaseSettings):
    """ConnectKit OAuth vault configuration.

    CONNECTKIT_VAULT_KEY is the Fernet key used to encrypt the credential vault
    (data/private/connectkit/). If unset, connectkit falls back to an ephemeral
    in-memory key — credentials are NOT persisted across restarts. Production
    must set it (see docs/RELEASE.md, README index "CONNECTKIT_VAULT_KEY is a
    production config requirement").
    """

    vault_key: str = Field(default="", description="Fernet key for the ConnectKit credential vault")

    model_config = SettingsConfigDict(env_prefix="CONNECTKIT_")


class ShellToolConfig(_BaseSettings):
    """Shell tool configuration."""

    enabled: bool = True
    allowed_commands: list[str] = Field(
        default_factory=lambda: ["python3", "node", "echo", "date", "whoami", "pwd"]
    )
    timeout_seconds: int = 30
    max_output_kb: int = 100

    model_config = SettingsConfigDict(env_prefix="SHELL_TOOL_")


class EmailSyncConfig(_BaseSettings):
    """Email sync configuration."""

    enabled: bool = True
    interval_minutes: int = 5
    batch_size: int = 100
    backfill_limit: int = 1000

    model_config = SettingsConfigDict(env_prefix="EMAIL_SYNC_")


class SchedulerConfig(_BaseSettings):
    """Agent scheduler configuration."""

    enabled: bool = False

    model_config = SettingsConfigDict(env_prefix="COMPANION_")


class MCPConfig(_BaseSettings):
    """MCP (Model Context Protocol) configuration."""

    enabled: bool = True
    idle_timeout_minutes: int = 30

    model_config = SettingsConfigDict(env_prefix="MCP_")


class TelemetryConfig(_BaseSettings):
    """Owner telemetry sidecar (Phase 2 D1-1). OFF by default: self-hosters
    opt in explicitly (TELEMETRY_ENABLED); opt-out is the shipped stance."""

    enabled: bool = False

    model_config = SettingsConfigDict(env_prefix="TELEMETRY_")


class MeteringConfig(_BaseSettings):
    """Usage metering (Phase 2 M1.1). OFF by default: the OSS sink is a no-op
    unless explicitly enabled per deployment (METERING_ENABLED)."""

    enabled: bool = False
    window_days: int = 30

    model_config = SettingsConfigDict(env_prefix="METERING_")


class PricingConfig(_BaseSettings):
    """Pricing plan defaults (Phase 2 M3-1). Seat/subscription prices are
    deployment config (env PRICING_* or yaml); per-tenant overrides live in
    tenant.db. The budget ENFORCEMENT threshold itself is per-tenant
    (tenants.monthly_budget_usd); these defaults only seed new tenants."""

    # Motion A (per-seat) defaults, USD per seat per month
    seat_price_usd: float = 25.0
    # Motion B (SMB subscription) monthly price
    smb_price_usd: float = 199.0
    # Platform-fee margin fraction (Motion C) — informational for price lab
    platform_fee_pct: float = 0.15
    # Default monthly usage cap applied to new tenants when unset (None = no
    # cap; enforcement also honors per-tenant monthly_budget_usd)
    default_usage_cap_usd: float | None = None

    model_config = SettingsConfigDict(env_prefix="PRICING_")


class AppConfig(_BaseSettings):
    """Main application configuration."""

    agent: AgentConfig = Field(default_factory=AgentConfig)
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    hill_climbing: HillClimbingConfig = Field(default_factory=HillClimbingConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    cli: CliConfig = Field(default_factory=CliConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    filesystem: FilesystemConfig = Field(default_factory=FilesystemConfig)
    shell_tool: ShellToolConfig = Field(default_factory=ShellToolConfig)
    email_sync: EmailSyncConfig = Field(default_factory=EmailSyncConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    metering: MeteringConfig = Field(default_factory=MeteringConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    companion: SchedulerConfig = Field(default_factory=SchedulerConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    connectkit: ConnectKitConfig = Field(default_factory=ConnectKitConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_nested_delimiter="__"
    )

    @property
    def deployment_mode(self) -> str:
        return self.deployment.mode

    @property
    def data_path(self) -> str:
        return self.deployment.data_path

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "AppConfig":
        """Load configuration from YAML file.

        The path defaults to ``config.yaml`` at the repository root (resolved
        from this file, not the process CWD) so every process finds it
        regardless of where it was launched. A missing file falls back to
        defaults with a warning — callers that rely on the defaults should
        not silently get a stale model.
        """
        if path is None:
            path = Path(__file__).resolve().parents[2] / "config.yaml"
        path = Path(path)
        if not path.exists():
            import logging

            logging.getLogger(__name__).warning(
                "config.yaml not found at %s — using defaults", path
            )
            config = cls()
            validate_startup_model_references(config)
            return config

        with open(path) as f:
            data = yaml.safe_load(f)

        if not data:
            config = cls()
            validate_startup_model_references(config)
            return config

        # Bare AGENT (set by opencode/agent runtimes) collides with the nested agent config field.
        _drop_colliding_env()
        config = cls(**data)
        # Langfuse behavior belongs under observability in YAML, while its
        # credentials continue to arrive through LANGFUSE_* environment vars.
        if isinstance(data.get("observability"), dict) and "langfuse" in data["observability"]:
            behavior = config.observability.langfuse
            config.langfuse.enabled = behavior.enabled
            config.langfuse.host = behavior.host
            config.langfuse.environment = behavior.environment
        validate_startup_model_references(config)
        return config


def validate_model_reference(
    model_ref: str, *, role: str, allow_legacy_syntax: bool = False
) -> tuple[str, str]:
    """Validate one deployment model reference without rejecting custom models."""
    value = model_ref.strip()
    if not value:
        raise ValueError(f"Invalid {role} model reference: value is empty")
    separator = ":" if ":" in value else "/" if allow_legacy_syntax and "/" in value else None
    if separator is None:
        if allow_legacy_syntax and value:
            return "ollama", value
        raise ValueError(
            f"Invalid {role} model reference {model_ref!r}: expected 'provider:model'"
        )
    provider, model = (part.strip() for part in value.split(separator, 1))
    if not provider or not model:
        raise ValueError(
            f"Invalid {role} model reference {model_ref!r}: expected non-empty 'provider:model'"
        )

    if model.endswith("-cloud") and not provider.endswith("-cloud"):
        logging.getLogger(__name__).warning(
            "Suspicious %s model reference %r; check whether the provider/model separator is misplaced",
            role,
            model_ref,
        )
    return provider.lower(), model


def validate_startup_model_references(config: AppConfig) -> None:
    """Validate effective deployment model references at application startup.

    allow_legacy_syntax=True: a deployment copying a models.dev style
    `provider/model` ref must boot — the runtime already accepts it. Malformed
    references (no separator, empty provider/model) still hard-fail.
    """
    if config.agent.model:
        validate_model_reference(config.agent.model, role="agent", allow_legacy_syntax=True)
    effective_agent = config.agent.model
    for role, configured in (
        ("title", config.agent.title_model),
        ("grader", config.verification.grader_model),
        ("summarization", config.memory.summarization.model),
    ):
        effective = configured or effective_agent
        if effective:
            validate_model_reference(effective, role=role, allow_legacy_syntax=True)


def warn_unknown_model_providers(config: AppConfig) -> None:
    """Warn for providers absent from models.dev without rejecting custom pulls."""
    from src.sdk.registry import get_provider

    references = {
        "agent": config.agent.model,
        "title": config.agent.title_model or config.agent.model,
        "grader": config.verification.grader_model or config.agent.model,
        "summarization": config.memory.summarization.model or config.agent.model,
    }
    for role, model_ref in references.items():
        if not model_ref:
            continue
        provider, _ = validate_model_reference(model_ref, role=role)
        if get_provider(provider) is None:
            logging.getLogger(__name__).warning(
                "Unknown provider type %r in %s model reference %r; allowing custom provider",
                provider,
                role,
                model_ref,
            )


_config: AppConfig | None = None

# Env vars that collide with nested AppConfig fields when set bare by external runtimes.
# opencode injects AGENT=1; pydantic would try to coerce it into AgentConfig and crash.
_COLLIDING_ENV = {"AGENT"}


def _drop_colliding_env() -> None:
    """Remove bare env vars that would clobber nested config fields."""
    import os

    for key in _COLLIDING_ENV:
        os.environ.pop(key, None)


def get_settings() -> AppConfig:
    """Get application settings singleton."""
    global _config
    if _config is None:
        # No argument -> repo-root resolution per from_yaml's contract
        # (audit E23: a relative "config.yaml" silently missed when launched
        # from any other directory).
        _config = AppConfig.from_yaml()
        # pydantic-settings gives init kwargs (yaml data) precedence over
        # environment variables; deployment contracts (docker-compose sets
        # API_PORT/API_HOST) must win, so apply them explicitly (audit E22).
        host = os.environ.get("API_HOST")
        port = os.environ.get("API_PORT")
        if host:
            _config.api.host = host
        if port and port.isdigit():
            _config.api.port = int(port)
        # Same E22 class of fix-up for agent models: flat AGENT_MODEL /
        # AGENT_TITLE_MODEL never match pydantic-settings nested-env rules
        # (they'd need AGENT__MODEL, and even that loses to init kwargs from
        # yaml). Deployments document AGENT_MODEL as the deployment-model
        # contract (D0-5) — wire it explicitly, env beats yaml.
        env_agent = os.environ.get("AGENT_MODEL")
        if env_agent:
            _config.agent.model = env_agent
        env_title = os.environ.get("AGENT_TITLE_MODEL")
        if env_title:
            _config.agent.title_model = env_title
    return _config


def reload_settings() -> AppConfig:
    """Reload settings (useful for testing)."""
    global _config
    _config = None
    return get_settings()
