"""Settings module for Assistant."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import Field, PrivateAttr, field_validator
from pydantic_settings import BaseSettings, NoDecode, PydanticBaseSettingsSource, SettingsConfigDict

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
    session_lease_timeout_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Idle time after which an active session run receives a cancellation request.",
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
    max_iterations: int = Field(default=25, ge=1, le=100)

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
    max_summary_chars: int = Field(
        default=24_000,
        ge=1_000,
        le=200_000,
        description="Hard character budget for persisted conversation summaries.",
    )
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
    # item-level permissions: tools/skills/subagents -> allow/ask/deny
    permissions: dict[str, dict[str, str]] = Field(default_factory=dict)
    operation_callback_secret: str = Field(
        default="",
        description="Required deployment secret for external governed-operation callbacks.",
    )
    external_executor_allowed_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Deployment allowlist of host or host:port values permitted for "
            "external governed-operation dispatch. Empty fails closed."
        ),
    )

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


class OtelConfig(_BaseSettings):
    """Admin-channel OTLP export configuration (OB-0).

    Empty endpoint = no exporter is ever constructed (fail-closed).
    Vendor/telemetry consent channels are out of scope here.
    """

    endpoint: str = ""
    headers: dict[str, str] = Field(default_factory=dict)

    model_config = SettingsConfigDict(env_prefix="OTEL_")


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
    otel: OtelConfig = Field(default_factory=OtelConfig)


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


class OidcConfig(_BaseSettings):
    """SSO via OIDC (Phase 3 T3.3). Off by default — zero behavior change.

    Deployment knobs are env vars (OIDC_ISSUER, OIDC_CLIENT_ID,
    OIDC_CLIENT_SECRET, OIDC_REDIRECT_URI); the yaml section is optional.
    """

    enabled: bool = Field(default=False)
    issuer: str = Field(default="", description="IdP issuer URL (discoverable)")
    client_id: str = Field(default="")
    client_secret: str = Field(default="")
    redirect_uri: str = Field(
        default="", description="Empty = derived from the request base URL"
    )
    scope: str = Field(default="openid profile email")
    session_hours: float = Field(default=8.0)

    model_config = SettingsConfigDict(env_prefix="OIDC_")


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


class NativeToolsConfig(_BaseSettings):
    """Deployment hard ceiling for shipped native tool definitions."""

    mode: Literal["all", "selected", "none"] = "all"
    enabled: list[str] = Field(default_factory=list)


class ToolsConfig(_BaseSettings):
    """Tools configuration."""

    native: NativeToolsConfig = Field(default_factory=NativeToolsConfig)
    # Coarse family-level disable, borrowed from OpenCode's tools glob gate.
    # A name matching any pattern is never exposed to the model. This narrows
    # only: it can never re-admit a capability-disabled or governance-denied
    # tool. Globs are matched against tool names (`*` / `?`).
    disabled: list[str] = Field(default_factory=list)
    firecrawl_api_key: str = Field(default="", validation_alias="FIRECRAWL_API_KEY")
    firecrawl_base_url: str = Field(default="", validation_alias="FIRECRAWL_BASE_URL")
    max_retries: int = 3
    timeout: int = 30

    model_config = SettingsConfigDict(env_prefix="TOOLS_")


def _apply_native_tools_env_override(native: NativeToolsConfig) -> NativeToolsConfig:
    """Return a validated native-tool policy with explicit env overrides.

    YAML constructor values otherwise take precedence over nested settings, so
    this deployment control is merged explicitly on every ``from_yaml`` path.
    Native values in the repository ``.env`` apply when process environment
    values are absent; process environment wins over ``.env``. Invalid values
    are passed to Pydantic validation rather than ignored.
    """
    dotenv = dotenv_values(REPO_ROOT / ".env")
    effective = {
        key: value
        for key, value in dotenv.items()
        if key in {"TOOLS_NATIVE__MODE", "TOOLS_NATIVE__ENABLED"} and value is not None
    }
    effective.update(
        {
            key: os.environ[key]
            for key in ("TOOLS_NATIVE__MODE", "TOOLS_NATIVE__ENABLED")
            if key in os.environ
        }
    )

    override: dict[str, Any] = {}
    if "TOOLS_NATIVE__MODE" in effective:
        override["mode"] = effective["TOOLS_NATIVE__MODE"]
    if "TOOLS_NATIVE__ENABLED" in effective:
        raw_enabled = effective["TOOLS_NATIVE__ENABLED"]
        try:
            override["enabled"] = json.loads(raw_enabled)
        except json.JSONDecodeError:
            # Let the typed model produce the configuration validation error.
            override["enabled"] = raw_enabled
    if not override:
        return native
    return NativeToolsConfig.model_validate({**native.model_dump(), **override})


class SkillsConfig(_BaseSettings):
    """Skills configuration."""

    model_config = SettingsConfigDict(env_prefix="SKILLS_")


class FilesystemConfig(_BaseSettings):
    """Filesystem tools configuration."""

    enabled: bool = True
    max_file_size_mb: int = 10
    # NoDecode stops pydantic-settings JSON-parsing this list in the *source*,
    # which would otherwise reject `FILESYSTEM_ALLOWED_ROOTS=/home/me/project`
    # before the validator below ever runs.
    allowed_roots: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Extra directories the filesystem tools may read and write, "
        "in addition to the assistant's own data root. Use this to grant access to "
        "a project checkout without widening every tool: files_* still enforce "
        "the list, while shell_execute remains separately approval-gated.",
    )
    max_repeated_tool_calls: int = Field(
        default=3,
        ge=2,
        le=50,
        description="Identical (tool, arguments) calls allowed per run before the "
        "loop is stopped. A repeated call cannot produce a new result; without a "
        "cap, a confused agent can spend an entire token budget repeating itself.",
    )
    @field_validator("allowed_roots", mode="before")
    @classmethod
    def _split_allowed_roots(cls, value: object) -> object:
        """Accept a JSON list, or plain newline/comma separated paths.

        pydantic-settings parses list fields as JSON only, so
        ``FILESYSTEM_ALLOWED_ROOTS=/home/me/project`` would otherwise fail
        validation — and a path list that quietly fails to apply is a
        security problem, not a papercut. Anything genuinely unparseable is
        raised rather than dropped.
        """
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"filesystem.allowed_roots looks like JSON but did not parse: {exc}"
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValueError("filesystem.allowed_roots JSON must be a list of paths")
                return [str(item) for item in parsed]
            return [part.strip() for part in re.split(r"[,\n]", raw) if part.strip()]
        return value

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


class SandboxConfig(_BaseSettings):
    """SandboxBackend selection (R-SB1): null (tests) | soft (trusted default).

    Swapping isolation level is a config change, not a code change (SB1-3
    acceptance). Soft+UID (per-user OS-account drop) is the DEFAULT soft
    sandbox (decision 2026-09-03): each assistant user_id maps to its own
    OS uid — kernel-enforced cross-user filesystem separation even for
    trusted teams. 'shared' keeps the single-identity drop for dev.
    """

    backend: Literal["null", "soft", "bwrap", "runc"] = Field(
        default="soft",
        description="SandboxBackend: 'soft' (default) | 'bwrap' (hard, T3.4) | 'null' | 'runc' (stub)",
    )
    bwrap_rootfs: str = Field(
        default="",
        description=(
            "Absolute path to a curated, read-only Linux bwrap rootfs. "
            "Required for backend=bwrap; '/' is never accepted."
        ),
    )
    # SB1-2 security rule: no agent subprocess runs as root. uid_mode:
    # 'per_user' — every assistant user_id drops to its own mapped uid:gid
    # (kernel-enforced cross-user isolation; requires a root/CAP_SETUID
    # server). 'shared' — one non-root identity for all users (macOS/dev).
    uid_mode: Literal["per_user", "shared"] = Field(
        default="per_user",
        description="'per_user' (kernel-enforced per-user isolation) or 'shared'",
    )
    uid: int = Field(
        default=1000,
        description="Shared-mode drop UID (uid_mode=shared); env SANDBOX_UID overrides",
    )
    gid: int = Field(
        default=1000,
        description="Shared-mode drop GID (uid_mode=shared); env SANDBOX_GID overrides",
    )
    # Per-user mapping: uid = uid_base + (sha256(user_id) % uid_range).
    uid_base: int = Field(
        default=2000, description="First uid of the per-user sandbox range"
    )
    uid_range: int = Field(
        default=1000, description="Size of the per-user sandbox uid range"
    )
    env_allow: list[str] = Field(
        default_factory=list,
        description="Explicit environment variable names allowed in sandboxed child processes.",
    )

    model_config = SettingsConfigDict(env_prefix="SANDBOX_")


class ShellToolConfig(_BaseSettings):
    """Shell tool configuration."""

    enabled: bool = True
    allowed_commands: list[str] = Field(
        default_factory=lambda: ["echo", "date", "whoami", "pwd"]
    )
    timeout_seconds: int = 30
    max_output_kb: int = 100
    # Issue #32 part 3: the workspace write budget (RLIMIT_FSIZE) is its own
    # number. It was derived from max_output_kb * 8 (~800 KB at the default),
    # which killed legitimate file work (downloads, generated reports) and
    # silently coupled two unrelated knobs.
    max_write_mb: int = 64

    model_config = SettingsConfigDict(env_prefix="SHELL_TOOL_")


class EmailSyncConfig(_BaseSettings):
    """Email sync configuration."""

    enabled: bool = True
    interval_minutes: int = 5
    batch_size: int = 100
    backfill_limit: int = 1000

    model_config = SettingsConfigDict(env_prefix="EMAIL_SYNC_")


class MCPConfig(_BaseSettings):
    """MCP (Model Context Protocol) configuration."""

    enabled: bool = True
    idle_timeout_minutes: int = 30
    # Axis A decides *when* to defer: `auto`/`auto:N` measure the deferrable
    # definitions against the model's context window and behave as `search` or
    # `always`. Axis B (never | search | always) is the explicit override, plus
    # the v0.6.21 values direct|hybrid|proxy accepted one release (deprecated).
    exposure: Literal[
        "auto", "direct", "hybrid", "proxy", "never", "search", "always"
    ] = "auto"
    # Global globs. `include_tools` narrows everything; `exclude_tools` is
    # applied afterwards, so a name in both is excluded (Pi's ordering).
    include_tools: list[str] = Field(default_factory=list)
    exclude_tools: list[str] = Field(default_factory=list)
    # Per-tool exemption from deferral. Server-supplied meta.alwaysLoad is
    # ignored unless trust_server_exemptions is set (spec C3).
    always_load: list[str] = Field(default_factory=list)
    trust_server_exemptions: bool = False
    defer_with_missing_metadata: bool = True
    cache_ttl_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    refresh_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    max_result_chars: int = Field(default=20_000, ge=1_000, le=200_000)

    model_config = SettingsConfigDict(env_prefix="MCP_")


class SchedulingConfig(_BaseSettings):
    """Background scheduling of subagent work (#46).

    Off by default. Scheduled runs are unattended, so the surface is gated
    explicitly rather than enabled by installing a dependency.
    """

    subagent_enabled: bool = False

    model_config = SettingsConfigDict(env_prefix="SCHEDULING_")


class TelemetryConfig(_BaseSettings):
    """Owner telemetry sidecar (Phase 2 D1-1). OFF by default: self-hosters
    opt in explicitly (TELEMETRY_ENABLED); opt-out is the shipped stance."""

    enabled: bool = False

    model_config = SettingsConfigDict(env_prefix="TELEMETRY_")


class SessionLogConfig(_BaseSettings):
    """Session-event log (R-SL1) — opt-in, shipped off (fallback path unchanged)."""

    enabled: bool = False

    model_config = SettingsConfigDict(env_prefix="SESSION_LOG_")


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


_LEGACY_GOVERNANCE_TIER_MAP = {
    "allow": "allow",
    "autonomous": "allow",
    "ask": "ask",
    "explicit": "ask",
    "deny": "deny",
    "hard_block": "deny",
}


def _migrate_legacy_governance(data: dict[str, Any]) -> None:
    """Migrate the pre-0.6.18 governance.tiers key before model parsing."""
    governance = data.get("governance")
    if not isinstance(governance, dict) or "tiers" not in governance:
        return
    legacy = governance.get("tiers")
    if not isinstance(legacy, dict):
        raise ValueError("governance.tiers must be a mapping of tool names to permissions")
    permissions = governance.get("permissions")
    if permissions is not None and not isinstance(permissions, dict):
        raise ValueError("governance.permissions must be a mapping")
    current = dict(permissions or {})
    tools = dict(current.get("tools") or {})
    for tool_name, tier in legacy.items():
        if not isinstance(tool_name, str) or not isinstance(tier, str):
            raise ValueError("governance.tiers entries must map tool names to permissions")
        mapped = _LEGACY_GOVERNANCE_TIER_MAP.get(tier.strip().lower())
        if mapped is None:
            raise ValueError(
                f"unknown legacy governance.tiers value for {tool_name!r}: {tier!r}"
            )
        existing = tools.get(tool_name)
        if existing is not None and str(existing).strip().lower() != mapped:
            raise ValueError(
                f"conflicting governance.tiers and governance.permissions entries for {tool_name!r}"
            )
        tools[tool_name] = mapped
    current["tools"] = tools
    governance["permissions"] = current
    governance.pop("tiers", None)
    logging.getLogger(__name__).warning(
        "Migrated deprecated governance.tiers to governance.permissions.tools"
    )


_MISSING = object()


class AppConfig(_BaseSettings):
    """Main application configuration."""

    #: The raw config.yaml document this model was built from, so the env/yaml
    #: disagreement report compares against the file rather than the merge.
    _yaml_doc: dict[str, Any] = PrivateAttr(default_factory=dict)

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
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    email_sync: EmailSyncConfig = Field(default_factory=EmailSyncConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    metering: MeteringConfig = Field(default_factory=MeteringConfig)
    session_log: SessionLogConfig = Field(default_factory=SessionLogConfig)

    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    connectkit: ConnectKitConfig = Field(default_factory=ConnectKitConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    oidc: OidcConfig = Field(default_factory=OidcConfig)

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_nested_delimiter="__"
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Make the environment outrank config.yaml.

        pydantic-settings resolves sources with the FIRST one winning, and
        `from_yaml` passes the yaml document as init kwargs. With the default
        ordering that made every key present in config.yaml immune to the
        environment, silently defeating the documented priority
        (env > .env > config.yaml > defaults) and the deployment instructions in
        the release notes.

        Ordering here is (env, .env, yaml, secrets): the environment wins,
        .env is the next tier, and yaml still supplies everything unset. Later
        sources deep-merge beneath earlier ones, so a section like `mcp` is
        filled from yaml without clobbering the keys the environment set.
        """
        return (
            env_settings,
            dotenv_settings,
            init_settings,
            file_secret_settings,
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
        data: dict[str, Any] = {}
        if not path.exists():
            logging.getLogger(__name__).warning(
                "config.yaml not found at %s — using defaults", path
            )
        else:
            with open(path) as f:
                loaded = yaml.safe_load(f)
            if loaded:
                data = loaded

        _migrate_legacy_governance(data)
        yaml_doc = dict(data)

        # Bare AGENT (set by opencode/agent runtimes) collides with the nested agent config field.
        _drop_colliding_env()
        config = cls(**data)
        config._yaml_doc = yaml_doc
        # Native-tool policy is an explicit deployment control. Apply it after
        # every construction path and revalidate instead of mutating models.
        config.tools.native = _apply_native_tools_env_override(config.tools.native)
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


_DEFAULT_LANGFUSE_HOST = "https://cloud.langfuse.com"


def _resolve_langfuse_host(config: AppConfig) -> str:
    """Effective explicit Langfuse host, or "" when none was configured.

    Mirrors app_logging's resolution order; LANGFUSE_BASE_URL (the name
    users copy off the Langfuse setup page) is primary, LANGFUSE_HOST the
    legacy alias. The cloud default sentinel counts as NOT configured —
    treating it as configured is the footgun this validation exists to close.
    """
    host = (
        os.environ.get("LANGFUSE_BASE_URL")
        or os.environ.get("LANGFUSE_HOST")
        or config.langfuse.host
        or ""
    )
    if host.strip() == _DEFAULT_LANGFUSE_HOST:
        return ""
    return host.strip()


def validate_observability_settings(config: AppConfig) -> None:
    """Fail closed on an ambiguous Langfuse destination (OB-0).

    An operator who enables Langfuse with credentials but configures no
    host would silently ship traces to cloud.langfuse.com (the SDK
    default). Refuse to boot instead; the fix is one env var.
    Disabled or key-less Langfuse needs no host and always passes.
    """
    lf = config.langfuse
    if not (lf.enabled and lf.public_key and lf.secret_key):
        return
    if not _resolve_langfuse_host(config):
        raise ValueError(
            "LANGFUSE is enabled with credentials but no host is configured: "
            "set LANGFUSE_BASE_URL (or legacy LANGFUSE_HOST, or "
            "observability.langfuse.host in config.yaml). Refusing to "
            "default to cloud.langfuse.com."
        )


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


def _report_env_overrides(config: "AppConfig") -> list[str]:
    """Log every setting where the environment disagrees with config.yaml.

    The environment now outranks yaml (see `settings_customise_sources`). That
    is a behaviour change, and a behaviour change made silently is the same
    failure as the bug being fixed: a deployment could set an env var long ago,
    later change the yaml, and only discover at runtime that the env still
    wins. Reporting the disagreement at startup makes it visible instead.
    """
    from pydantic_settings.sources import EnvSettingsSource

    try:
        from_env = EnvSettingsSource(
            type(config), env_nested_delimiter=config.model_config.get(
                "env_nested_delimiter", "__"
            )
        )()
    except Exception as exc:  # pragma: no cover - reporting must never block
        logging.getLogger(__name__).debug("settings.env_report_failed: %s", exc)
        return []

    disagreements: list[str] = []

    yaml_doc = config._yaml_doc  # noqa: SLF001 - same module

    def _yaml_at(path: tuple[str, ...]) -> Any:
        """The value config.yaml supplies at this path, or a missing sentinel."""
        node: Any = yaml_doc
        for part in path:
            if not isinstance(node, dict) or part not in node:
                return _MISSING
            node = node[part]
        return node

    def _walk(node: Any, path: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            # Compare against what config.yaml supplied. Comparing against the
            # merged model would never fire: where the environment wins, the
            # model necessarily equals the environment.
            from_yaml_value = _yaml_at(path)
            if from_yaml_value is _MISSING:
                return  # environment supplies a new key; nothing to disagree with
            if str(from_yaml_value) != str(node):
                disagreements.append(
                    f"{'__'.join(path).upper()}={node} (config.yaml: {from_yaml_value})"
                )
            return
        for key, value in node.items():
            _walk(value, (*path, key))

    _walk(from_env, ())
    if disagreements:
        logging.getLogger(__name__).warning(
            "settings.env_overrides_yaml environment=%s config.yaml supplies the rest",
            ", ".join(sorted(disagreements)),
        )
    return sorted(disagreements)


def get_settings() -> AppConfig:
    """Get application settings singleton."""
    global _config
    if _config is None:
        # No argument -> repo-root resolution per from_yaml's contract
        # (audit E23: a relative "config.yaml" silently missed when launched
        # from any other directory).
        _config = AppConfig.from_yaml()
        # E22-class flat deployment contracts. These are deliberately NOT routed
        # through pydantic-settings: a flat name like API_PORT would have to be
        # API__PORT under the nested delimiter, and API_PORT / AGENT_MODEL /
        # OIDC_* are the documented deployment contract. They still beat yaml.
        host = os.environ.get("API_HOST")
        port = os.environ.get("API_PORT")
        if host:
            _config.api.host = host
        if port and port.isdigit():
            _config.api.port = int(port)
        # Same E22 class of fix-up for agent models: flat AGENT_MODEL /
        # AGENT_TITLE_MODEL never match pydantic-settings nested-env rules
        # (they'd need AGENT__MODEL). Deployments document AGENT_MODEL as the
        # deployment-model contract (D0-5) — wire it explicitly, env beats yaml.
        env_agent = os.environ.get("AGENT_MODEL")
        if env_agent:
            _config.agent.model = env_agent
        env_title = os.environ.get("AGENT_TITLE_MODEL")
        if env_title:
            _config.agent.title_model = env_title
        # OIDC deployment knobs: flat env beats yaml (same E22 class).
        for env_key, field_name in (
            ("OIDC_ISSUER", "issuer"),
            ("OIDC_CLIENT_ID", "client_id"),
            ("OIDC_CLIENT_SECRET", "client_secret"),
            ("OIDC_REDIRECT_URI", "redirect_uri"),
            ("OIDC_SCOPE", "scope"),
        ):
            val = os.environ.get(env_key)
            if val:
                setattr(_config.oidc, field_name, val)
        _report_env_overrides(_config)
        # OB-0: fail closed on an ambiguous Langfuse destination before any
        # client can be constructed against the cloud default.
        try:
            validate_observability_settings(_config)
        except ValueError:
            _config = None  # don't cache a half-validated singleton
            raise
    return _config


def reload_settings() -> AppConfig:
    """Reload settings (useful for testing)."""
    global _config
    _config = None
    return get_settings()
