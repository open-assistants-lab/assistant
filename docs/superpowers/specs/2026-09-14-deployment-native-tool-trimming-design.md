# Deployment Native Tool Trimming Design

## Goal
Allow a deployment to remove built-in native tools from every agent tool registry, reducing prompt schema/token cost for single-purpose deployments.

## Configuration
`config.yaml` supports:

```yaml
tools:
  disabled: [app_*, browser_*, files_*]
```

Patterns use case-sensitive shell-style glob matching (`fnmatchcase`); exact names are therefore supported. `TOOLS_DISABLED` follows normal Pydantic list environment parsing.

## Boundary
The filter applies only to definitions returned by `get_native_tools()`. It runs in the SDK runner before user capability filtering and applies to loop refresh catalogs too. It does not remove custom `TOOL.md` definitions, MCP definitions, or tool metadata API behavior outside the active agent registry.

## Precedence
Deployment disable is a hard ceiling: a user/workspace capability setting cannot restore a deployment-disabled native tool. Other native tools retain existing capability behavior. No deployment pattern disables a custom tool of the same name.

## Validation
Cover empty config, exact and glob patterns, hard-ceiling precedence, live-refresh catalog behavior, and custom-tool preservation. Run focused tests, then full suite before release.
