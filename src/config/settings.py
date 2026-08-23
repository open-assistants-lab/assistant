"""Settings module for Assistant."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    ea_root: str = Field(
        default="",
        description="Root for user data directory. Empty string means Path.home() / 'Assistant'.",
    )

    model_config = SettingsConfigDict(env_prefix="DEPLOYMENT_")


class AgentConfig(_BaseSettings):
    """Agent configuration."""

    name: str = Field(default="Assistant")
    model: str = Field(default="ollama-cloud:deepseek-v4-flash:0731")
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
    model: str = Field(default="ollama-cloud:deepseek-v4-flash:0731")
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


class AuthConfig(_BaseSettings):
    """API key authentication for remote connections.

    Solo (localhost): auth disabled if api_key is empty. Localhost bypass enabled by default.
    Multi-device WAN: set EA_API_KEY to require auth on non-localhost connections.
    Multi-tenant: each container has its own EA_API_KEY.
    """

    api_key: str = Field(default="")
    solo_bypass: bool = Field(default=True)
    # Trusted CORS origins, comma-separated (audit B17). Empty -> wildcard
    # origins WITHOUT credentials (safe default for local dev).
    cors_origins: str = Field(
        default="", description="Comma-separated trusted CORS origins"
    )

    model_config = SettingsConfigDict(env_prefix="EA_")


class ApiConfig(_BaseSettings):
    """API configuration."""

    host: str = "0.0.0.0"
    port: int = 8000
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
    """Email configuration for Gmail/Outlook via gws/m365 CLI."""

    enabled: bool = True
    gws_client_id: str = Field(default="")
    gws_client_secret: str = Field(default="")
    m365_client_id: str = Field(default="")
    sync_interval_minutes: int = Field(default=15)

    model_config = SettingsConfigDict(env_prefix="EMAIL_")


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


class AppConfig(_BaseSettings):
    """Main application configuration."""

    agent: AgentConfig = Field(default_factory=AgentConfig)
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
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
    companion: SchedulerConfig = Field(default_factory=SchedulerConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__")

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
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f)

        if not data:
            return cls()

        # Bare AGENT (set by opencode/agent runtimes) collides with the nested agent config field.
        _drop_colliding_env()
        return cls(**data)


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
        _config = AppConfig.from_yaml("config.yaml")
    return _config


def reload_settings() -> AppConfig:
    """Reload settings (useful for testing)."""
    global _config
    _config = None
    return get_settings()
