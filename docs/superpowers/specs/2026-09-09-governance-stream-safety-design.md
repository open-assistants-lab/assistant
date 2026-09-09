# Governance Stream Safety Design

## Goal
Restore mandatory middleware registration after the v0.6.2 regression and make a governance-blocked tool call terminal before either the sequential or parallel streaming/non-streaming execution path can invoke a tool body.

## Scope
This design implements GitHub issues #20 and #19. It deliberately preserves the existing parallel-safe versus sequential execution model: batching is a concurrency concern and sequencing is an ordering concern. Neither path may own authorization decisions.

## Defect #20: unreachable middleware registration
`create_sdk_loop()` defines the issue-#18 context-pruner helper and accidentally leaves `SummarizationMiddleware` and `HITLMiddleware` appends after its `return`. They are unreachable, producing an empty middleware list.

The context-pruner remains a local callback. The summarization middleware is appended in the summarization-enabled branch. The HITL middleware is appended independently when governance is enabled, so governance cannot be disabled by summarization configuration or a future summarization change.

## Defect #19: authorization must be a terminal shared stage
There are four tool executors: sequential and parallel-safe implementations for normal and stream runs. They currently duplicate argument wrapping and guard invocation. Introduce a private shared preparation result with either a transformed `ToolCall` permitted for execution or a synthetic `ToolResult` that is terminal.

`_prepare_tool_call()` performs, in order:
1. input guardrail check;
2. copy-and-transform middleware argument wrapping;
3. governance `guard_tool_call` evaluation.

A blocked result is converted into a model-visible tool result exactly once. No path calls `_execute_tool`, records the call as executed, or retries it. The batch paths use the same helper once per item before scheduling execution; the single paths use it once before execution. The existing explicit interrupt classification remains unchanged; it is a separate pre-execution UX path.

## Testing
Tests must instantiate a loop with governance enabled and a counting explicit-tier tool. Cover normal and streaming sequential and parallel-safe calls. Each blocked call must create one pending/guard decision, emit one blocked result (stream where applicable), and execute the tool body zero times. Separate runner construction tests assert independently enabled summarization and governance middleware registration.

## Release
Because #20 disables approval-required tools globally, ship a patch release immediately after focused governance, streaming, API, lint, and type gates pass. #19 joins that release only when its discriminator test passes.
