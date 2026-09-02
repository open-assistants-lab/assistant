"""Pydantic request/response models for the HTTP API."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.sdk.run_models import RunResult
from src.storage.paths import DEFAULT_USER_ID


class VerificationRequest(BaseModel):
    rubric: str | None = None
    # Selective verification (C11): per-request override of the configured
    # mode — "off" | "on" | "auto". None = use settings default.
    mode: Literal["off", "on", "auto"] | None = None


class MessageRequest(BaseModel):
    message: str
    model: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    verbose: bool = False
    provider_keys: dict[str, str] | None = None
    provider_options: dict[str, dict[str, Any]] | None = None
    verification: VerificationRequest | None = None

    @field_validator("provider_options")
    @classmethod
    def _allowlisted_provider_options(
        cls, value: dict[str, dict[str, Any]] | None
    ) -> dict[str, dict[str, Any]] | None:
        # Issue #10: no arbitrary request JSON reaches providers — only the
        # known-safe reasoning-control keys pass through per provider.
        if value is None:
            return value
        allowed = {
            "think",
            "chat_template_kwargs",
            "thinking",
            "thinkingConfig",
            "num_ctx",
        }
        for provider, opts in value.items():
            bad = set(opts) - allowed
            if bad:
                raise ValueError(
                    f"Unsupported provider options for {provider!r}: {sorted(bad)}. "
                    f"Allowed keys: {sorted(allowed)}"
                )
        return value


class VerificationVerdict(BaseModel):
    status: str | None = None
    iterations: int = 0
    attempts: int = 0
    max_attempts: int = 1
    explanation: str | None = None
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    evaluations: list[dict[str, Any]] = Field(default_factory=list)


class MessageResponse(BaseModel):
    response: str
    reasoning: str | None = None
    error: str | None = None
    verbose_data: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = Field(default=None)
    verification: VerificationVerdict | None = None
    usage: dict[str, Any] | None = None
    run: RunResult | None = None


class MemorySearchRequest(BaseModel):
    query: str
    method: str = "hybrid"
    limit: int = 10
    user_id: str =  DEFAULT_USER_ID


class InsightSearchRequest(BaseModel):
    query: str
    method: str = "hybrid"
    limit: int = 5
    user_id: str =  DEFAULT_USER_ID


class SearchAllRequest(BaseModel):
    query: str
    memories_limit: int = 5
    messages_limit: int = 5
    insights_limit: int = 3
    user_id: str =  DEFAULT_USER_ID


class ConnectionRequest(BaseModel):
    memory_id: str
    target_id: str
    relationship: str = "relates_to"
    strength: float = 1.0
    user_id: str =  DEFAULT_USER_ID


class EmailConnectRequest(BaseModel):
    email: str
    password: str
    provider: str | None = None
    user_id: str =  DEFAULT_USER_ID
