"""Pydantic request/response models for the HTTP API."""

from typing import Any

from pydantic import BaseModel, Field

from src.sdk.run_models import RunResult


class VerificationRequest(BaseModel):
    rubric: str | None = None


class MessageRequest(BaseModel):
    message: str
    model: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    verbose: bool = False
    provider_keys: dict[str, str] | None = None
    verification: VerificationRequest | None = None


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
    user_id: str = "default_user"


class InsightSearchRequest(BaseModel):
    query: str
    method: str = "hybrid"
    limit: int = 5
    user_id: str = "default_user"


class SearchAllRequest(BaseModel):
    query: str
    memories_limit: int = 5
    messages_limit: int = 5
    insights_limit: int = 3
    user_id: str = "default_user"


class ConnectionRequest(BaseModel):
    memory_id: str
    target_id: str
    relationship: str = "relates_to"
    strength: float = 1.0
    user_id: str = "default_user"


class EmailConnectRequest(BaseModel):
    email: str
    password: str
    provider: str | None = None
    user_id: str = "default_user"
