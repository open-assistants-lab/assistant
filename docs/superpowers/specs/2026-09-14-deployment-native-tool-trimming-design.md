# Deployment Native Tool Policy Design

## Goal
Let a deployment declare which shipped native tools may enter an agent registry. This reduces tool-schema cost and ensures future native tools are not silently exposed in single-purpose deployments.

## Configuration

```yaml
tools:
  native:
    mode: none        # all | selected | none
    enabled: []       # case-sensitive exact/glob patterns; selected only
```

`mode: all` is the general-purpose default. `mode: none` exposes no shipped native tools. `mode: selected` exposes only native names matching `enabled` patterns, using `fnmatchcase`. Environment values use the ordinary Pydantic nested-settings form: `TOOLS_NATIVE__MODE` and JSON-list `TOOLS_NATIVE__ENABLED`.

## Boundary
The policy applies to all shipped native definitions, including runner-added meta tools. It is a hard ceiling at registry creation, refresh, persisted search, lazy load, direct execution, and prompt guidance. Custom per-tool `TOOL.md` definitions and MCP tools are independent extension planes and remain available even if their names match a native pattern.

## Naming
`TOOL.md` is the singular, per-custom-tool definition format. A future generated `TOOLS.md` catalog may be informational only; it is never an authorization source.

## Validation
Cover all/selected/none, exact/glob patterns, YAML/env precedence, native-only collisions, creation/refresh/search/lazy/direct execution/prompt guidance, and default-safe behavior.
