# Deep Agents Analysis — What EA Should Learn & Adopt

Source: [LangChain Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) (built on LangChain + LangGraph). "Agent harness" with built-in tools for planning, filesystem, subagents, skills, memory, human-in-the-loop, and sandbox execution.

---

## Feature Comparison: Deep Agents vs EA

| Capability | Deep Agents | EA | Verdict |
|-----------|-------------|-----|---------|
| **Agent loop** | LangGraph ReAct | `AgentLoop` (ReAct) | Parity |
| **Task planning** | `write_todos` tool | `todos_*` tools | Parity |
| **File tools** | ls, read_file, write_file, edit_file, glob, grep | `files_*` (7 tools) | Parity |
| **Subagents** | `task` tool, sync + async | `SubagentCoordinator` + `WorkQueueDB` | Parity |
| **HITL** | `interrupt_on` config | `Interrupt` in AgentLoop | Parity |
| **Skills** | Agent Skills standard (progressive disclosure) | `SkillMiddleware` + `SkillRegistry` | Parity |
| **Long-term memory** | Store backend (cross-thread) | HybridDB + MemoryMiddleware | EA stronger |
| **Context summarization** | Auto at 85% window | `SummarizationMiddleware` | Parity |
| **Context offloading** | Auto-offload large tool I/O → filesystem references | — | **Gap** |
| **Pluggable backends** | State, Filesystem, Store, Composite, Sandbox | HybridDB (tightly coupled to filesystem) | **Gap** |
| **Sandbox execution** | Modal, Daytona, Runloop, AgentCore | `shell_execute` (host, no isolation) | **Gap** |
| **Interpreters** | QuickJS for programmatic tool composition | — | **Gap** |
| **Filesystem permissions** | Declarative path-level read/write rules | `ToolAnnotations` (tool-level only) | **Gap** |
| **Structured subagent output** | `response_format` (Pydantic model → JSON) | Free-form text only | **Gap** |
| **Harness profiles** | Per-provider/model tool/middleware bundles | `provider_options` (less structured) | **Gap** |
| **Multimodal file reading** | Images, video, audio, PDFs native in read_file | Text only | **Gap** |
| **Provider-agnostic** | Google, OpenAI, Anthropic, OpenRouter, Fireworks, Baseten, Ollama | Same via `LLMProvider` abstraction | Parity |

---

## What EA Should Adopt

### Priority 1 — Genuine Gaps With High Impact

#### 1. Context Offloading

Deep Agents auto-offloads large tool inputs/outputs (threshold: 20,000 tokens) to filesystem, replacing them with reference pointers and a 10-line preview. This prevents a single large file read or tool result from consuming the entire context window.

Assistant's `SummarizationMiddleware` compresses conversation history but doesn't handle individual large tool results. If `files_read` returns a 50KB file, that content sits in the conversation until summarization kicks in — and by then it's already consumed the window.

**How to adopt:** Add `ContentOffloadMiddleware` that wraps tool calls. When tool output exceeds a threshold (e.g., 10,000 tokens), save it to `data/users/{user_id}/offload/{tool}_{call_id}.txt`, replace the tool message with `[Content saved to /offload/...] First 10 lines: ...`. The agent can re-read via `files_read` if needed.

**Lines:** ~150. **Effort:** 1 day.

#### 2. Structured Subagent Output

Deep Agents' `response_format` forces subagents to return parseable JSON via Pydantic models. Assistant's subagents return free-form text — the parent must parse or trust the content.

**How to adopt:** Add `response_format` field to `AgentDef`. When set, the `SubagentCoordinator` passes the schema to the subagent's `AgentLoop.run()` and validates the output before returning to the parent.

**Lines:** ~80 in coordinator.py. **Effort:** ½ day.

#### 3. Sandbox Execution

Deep Agents wraps the `execute` tool to run in isolated environments (Modal, Daytona, Runloop). Assistant's `shell_execute` runs directly on the host system.

**Verdict: Not applicable for solo desktop.** For a solo user, `shell_execute` on the host is the intended behavior — you own the machine, you trust the agent. Isolation adds complexity with zero benefit. For multi-tenant deployments, the Docker setup in `DEPLOYMENT.md` already provides container-level isolation per user. Skip this.

### Priority 2 — Nice to Have

#### 4. Pluggable Filesystem Backend Interface

Deep Agents' `CompositeBackend` routes virtual paths to different storage backends (`/skills/` → Store, `/memories/` → State, `/workspace/` → Sandbox). Assistant's HybridDB uses the physical filesystem directly — no routing, no swappable backends.

**How to adopt:** Extract a `StorageBackend` protocol from HybridDB. Implement `LocalDiskBackend` (current behavior) and `InMemoryBackend` (for testing). Wire through `_connect()`, `_init_chroma()`, and `_get_embedding()`. This is a refactor, not a feature — makes testing easier and enables future remote backends.

**Lines:** ~300 refactor. **Effort:** 2 days.

#### 5. Harness Profiles

Deep Agents' `HarnessProfile` bundles per-model defaults: system prompt, excluded tools, tool description overrides, middleware adjustments. When switching from Claude to Gemini, the profile auto-adjusts middleware. EA has `provider_options` but it only passes key-value metadata — no tool or middleware control.

**How to adopt:** Extend `RunConfig.provider_options` to include `excluded_tools`, `tool_description_overrides`, and `middleware_additions`. Look up by provider_id at `AgentLoop` creation. This enables model-specific tuning without code changes.

**Lines:** ~100. **Effort:** 1 day.

#### 6. Filesystem Permissions

Deep Agents' declarative rules (`allow read /workspace/*`, `deny write *.env`, first-match-wins) are applied to all filesystem tools. EA has `ToolAnnotations.destructive`/`readOnly` but no path-level access control.

**How to adopt:** Add a `FilesystemPolicy` model with `operations`, `paths` (glob), and `mode` (allow/deny). Pass to `CLIToolAdapter` and `files_*` tools. Evaluate in declaration order before every file operation. Useful for restricting subagent access.

**Lines:** ~100. **Effort:** 1 day.

### Priority 3 — Future

#### 7. Interpreters

QuickJS runtime lets agents compose tools programmatically — batch processing, data transformation, tool orchestration — without spawning shell processes. Useful but niche.

#### 8. Multimodal File Reading

Support images, video, audio, PDFs in `files_read`. Requires model support (Gemini, GPT-4V). Assistant's current models (Ollama) don't support this well. Wait until multimodal models are standard in EA.

---

## What NOT to Adopt

| Deep Agents feature | Why skip |
|---------------------|----------|
| **LangChain/LangGraph dependency** | EA replaced this with custom SDK. Deep Agents is built on top. Not relevant. |
| **System prompt assembly** | Assistant's middleware pattern (each middleware appends to system prompt) is equivalent to Deep Agents' layered prompt. |
| **Memory model** | Assistant's HybridDB + MemoryMiddleware + graph is more capable than Deep Agents' Store-backed `/memories/` files. |
| **Streaming model** | Assistant's block-structured streaming (text_start/delta/end, tool_input_start/delta/end) is equivalent. |
| **Checkpointer/durable execution** | Assistant's MessageStore + HybridDB provides durable state. Deep Agents uses LangGraph checkpointing. |

---

## Recommended Implementation Order

| Priority | Feature | Lines | Days | Impact |
|----------|---------|-------|------|--------|
| **P1** | Context offloading | ~150 | 1 | Prevents single large tool calls from consuming context window |
| **P1** | Structured subagent output | ~80 | 0.5 | Makes subagent results machine-parseable, enables routing |
| **P2** | Pluggable backend interface | ~300 | 2 | Enables in-memory testing, future remote backends |
| **P2** | Harness profiles | ~100 | 1 | Model-specific tool/middleware tuning without code changes |
| **P2** | Filesystem permissions | ~100 | 1 | Path-level access control for subagents |
| **P3** | Interpreters (QuickJS) | — | — | Wait until tool composition patterns emerge |
| **P3** | Multimodal file reading | — | — | Wait until multimodal models are standard in EA |
