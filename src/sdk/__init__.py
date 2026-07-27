"""Custom Agent SDK - message types, tools, providers, and agent loop.

Public API:
    Message, ToolCall, StreamChunk - message types
    tool, ToolRegistry, ToolDefinition, ToolAnnotations, ToolResult - tool system
    AgentState - agent loop state
    LLMProvider, ModelInfo, ModelCost - provider base types
    create_provider, create_model_from_config - factory functions
    OllamaCloud - Ollama Cloud provider (native /api/chat)
    OpenAIProvider, AnthropicProvider, GeminiProvider - other providers
    AgentLoop, Interrupt, RunConfig, CostTracker, Usage - agent loop
    Middleware - middleware base class
    SummarizationMiddleware - conversation summarization (SDK-native)
    SubagentContext - in-memory subagent signaling (replaces middleware-based progress/instruction)
    SubagentCancelledError - raised by AgentLoop on subagent cancel
    HookManager, HookConfig, HookResult, HookDecision - shell hooks
    InputGuardrail, OutputGuardrail, ToolGuardrail, GuardrailResult, GuardrailTripwire - guardrails
    Handoff, HandoffInput - handoffs
    TraceProvider, TraceProcessor, Span, SpanType - tracing
    normalize_tool_schema, repair_tool_call - validation
    get_model_info, list_models, get_provider, list_providers, refresh - models.dev registry
    HybridDB, SearchMode, EmbeddingModelError - hybrid search database
    AgentProfile - portable agent definition (from agentprofile OSS package)
    SubagentResult, TaskStatus, TaskCancelledError - subagent models
    WorkQueueDB, get_work_queue - work queue database
    SubagentCoordinator, get_coordinator - subagent coordination
"""

from agentprofile.models import AgentProfile
from hybriddb import EmbeddingModelError, HybridDB, SearchMode

from src.sdk.coordinator import SubagentCoordinator, get_coordinator
from src.sdk.guardrails import (
    GuardrailResult,
    GuardrailTripwire,
    InputGuardrail,
    OutputGuardrail,
    ToolGuardrail,
)
from src.sdk.handoffs import Handoff, HandoffInput
from src.sdk.loop import AgentLoop, CostTracker, Interrupt, RunConfig
from src.sdk.messages import Message, StreamChunk, ToolCall, Usage
from src.sdk.middleware import Middleware
from src.sdk.middleware_summarization import SummarizationMiddleware
from src.sdk.providers.base import LLMProvider, ModelCost, ModelInfo
from src.sdk.providers.factory import create_model_from_config, create_provider
from src.sdk.providers.ollama import OllamaCloud
from src.sdk.registry import get_model_info, get_provider, list_models, list_providers, refresh
from src.sdk.state import AgentState
from src.sdk.subagent_context import SubagentCancelledError, SubagentContext
from src.sdk.subagent_models import (
    CostLimitExceededError,
    MaxCallsExceededError,
    SubagentResult,
    TaskCancelledError,
    TaskStatus,
)
from src.sdk.tools import ToolAnnotations, ToolDefinition, ToolRegistry, ToolResult, tool
from src.sdk.tracing import (
    ConsoleTraceProcessor,
    JsonTraceProcessor,
    Span,
    SpanType,
    TraceProcessor,
    TraceProvider,
)
from src.sdk.validation import normalize_tool_schema, repair_tool_call
from src.sdk.work_queue import WorkQueueDB, get_work_queue

__all__ = [
    "Message",
    "ToolCall",
    "StreamChunk",
    "tool",
    "ToolRegistry",
    "ToolDefinition",
    "ToolAnnotations",
    "ToolResult",
    "Usage",
    "AgentState",
    "LLMProvider",
    "ModelInfo",
    "ModelCost",
    "create_provider",
    "create_model_from_config",
    "OllamaCloud",
    "get_model_info",
    "list_models",
    "get_provider",
    "list_providers",
    "refresh",
    "AgentLoop",
    "Interrupt",
    "RunConfig",
    "CostTracker",
    "Middleware",
    "SubagentContext",
    "SubagentCancelledError",
    "SummarizationMiddleware",
    "HookManager",
    "HookConfig",
    "HookResult",
    "HookDecision",
    "InputGuardrail",
    "OutputGuardrail",
    "ToolGuardrail",
    "GuardrailResult",
    "GuardrailTripwire",
    "Handoff",
    "HandoffInput",
    "HybridDB",
    "SearchMode",
    "EmbeddingModelError",
    "TraceProvider",
    "TraceProcessor",
    "ConsoleTraceProcessor",
    "JsonTraceProcessor",
    "Span",
    "SpanType",
    "normalize_tool_schema",
    "repair_tool_call",
    "AgentProfile",
    "SubagentResult",
    "TaskStatus",
    "TaskCancelledError",
    "MaxCallsExceededError",
    "CostLimitExceededError",
    "WorkQueueDB",
    "get_work_queue",
    "SubagentCoordinator",
    "get_coordinator",
]
