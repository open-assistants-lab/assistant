# MCP Proxy-First Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add proxy-first MCP discovery and invocation with durable metadata caching, explicit direct-tool promotion, bounded results, and read-only status snapshots while preserving the existing lifecycle/receipt contracts.

**Architecture:** Extend the existing `MCPManager` as the single owner of live connections and cached metadata. Add a governed `mcp` proxy tool for search/describe/call/status. Keep current direct registration behind a compatibility mode, then promote only explicitly selected tools in hybrid mode. The existing execution kernel, permission policy, receipt model, and MCP lifecycle design remain authoritative.

**Tech Stack:** Python 3.11–3.13, FastAPI, Pydantic v2, aiosqlite/SQLite, `mcp` SDK, pytest, Ruff, mypy, local stdio fixture servers.

**Specs:**
- `docs/superpowers/specs/2026-09-25-mcp-lifecycle-reliability-design.md`
- `docs/superpowers/specs/2026-09-25-mcp-proxy-architecture-design.md`

## Global Constraints

- Do not create a second MCP client/session owner.
- Lazy servers remain disconnected for search/describe when cached metadata exists.
- Proxy calls re-resolve the final server/tool and apply current `allow`/`ask`/`deny` policy before dispatch.
- Missing, invalid, disabled, stale, and unavailable configurations remain distinct.
- Cache/status records never contain headers, bearer tokens, OAuth state, or environment values.
- Proxy and direct calls produce equivalent outcome/receipt semantics.
- MCP transport-security host allowlists and full OAuth remain out of scope.
- Existing direct MCP names remain available in compatibility mode.
- TDD and full-suite verification are required.

## File Map

### Lifecycle and configuration

- Modify `src/sdk/tools_core/mcp_manager.py`: generations, candidate cleanup, reconciliation, cache ownership, status snapshots, explicit destruction.
- Modify `src/sdk/tools_core/mcp_config.py`: source/provenance models and safe configuration-state helpers.
- Modify `src/http/routers/mcp.py`: stable status snapshot contract.
- Modify `src/sdk/runner.py`: bridge cache/direct-tool lifecycle integration.
- Test `tests/sdk/test_mcp_lifecycle.py`, `tests/sdk/test_mcp.py`, `tests/api/test_mcp_health.py`.

### Proxy and cache

- Create `src/sdk/tools_core/mcp_proxy.py`: governed proxy tool and action dispatch.
- Modify `src/sdk/tools_core/mcp.py`: export/register the proxy tool and keep legacy meta-tools.
- Create `src/sdk/tools_core/mcp_cache.py`: durable metadata cache and invalidation.
- Modify `src/config/settings.py`: `MCPConfig.exposure`, `direct_tools`, cache/refresh/output limits.
- Modify `src/sdk/tools_core/mcp_bridge.py`: proxy mapping, cache-backed search/describe, direct promotion/removal.
- Test `tests/sdk/test_mcp_proxy.py`, `tests/sdk/test_mcp_cache.py`, `tests/sdk/test_mcp_bridge.py`.

### Output and rollout

- Create `src/sdk/tools_core/mcp_results.py`: focused MCP output guard helper.
- Modify `seeds/skills/cli-toolkit/SKILL.md` if the proxy surface changes documented authoring guidance.
- Modify `CHANGELOG.md` only at release time.

---

## Task 1: Implement MCP lifecycle foundations

**Files:**
- Modify: `src/sdk/tools_core/mcp_manager.py`
- Modify: `src/sdk/tools_core/mcp_config.py`
- Modify: `src/http/routers/mcp.py`
- Test: `tests/sdk/test_mcp_lifecycle.py`
- Test: `tests/api/test_mcp_health.py`

**Interfaces:**
- Produces `MCPManager._lifecycle_generation: int`.
- Produces `MCPManager._ensure_current_config() -> None`.
- Produces `MCPManager.reap_stale(max_idle_seconds: int) -> list[str]`.
- Produces `close_mcp_manager(user_id: str) -> None`.
- Preserves `/mcp/health` and `/v1/mcp/health` route shapes.

- [ ] **Step 1: Write failing lifecycle tests.**

Add tests covering candidate cleanup, config deletion, invalid config, late reconnect, manager destruction, and status reads without starting a server:

```python
async def test_failed_tool_discovery_closes_candidate_without_publishing(manager):
    candidate = FakeConnection(list_tools_error=RuntimeError("discovery failed"))
    manager._create_connection = AsyncMock(return_value=candidate)
    await manager._start_server("demo", server_config())
    assert manager._connections == {}
    assert candidate.closed is True


async def test_deleted_config_stops_existing_connection(manager):
    manager._connections["demo"] = FakeConnection()
    manager._config_mtime = 1.0
    with patch("src.sdk.tools_core.mcp_manager.get_config_mtime", return_value=2.0), patch(
        "src.sdk.tools_core.mcp_manager.load_mcp_config", return_value=None
    ):
        manager._ensure_current_config()
    assert manager._connections == {}


async def test_status_read_does_not_start_lazy_server(manager):
    with patch.object(manager, "_ensure_started", new=AsyncMock()) as ensure:
        await manager.health()
    ensure.assert_not_awaited()
```

- [ ] **Step 2: Run the lifecycle tests red.**

```bash
uv run pytest -q tests/sdk/test_mcp_lifecycle.py tests/api/test_mcp_health.py
```

Expected failures must identify the missing generation, cleanup, reconciliation, or health behavior.

- [ ] **Step 3: Implement candidate publication and cleanup.**

Change startup so `_create_connection()` and `list_tools()` complete before insertion into `_connections`. Wrap the candidate in one close-on-failure path and ensure the close path is idempotent.

- [ ] **Step 4: Implement centralized reconciliation.**

Add `_ensure_current_config()` and invoke it from `get_tools`, `ensure_connection`, `list_servers`, `health`, and `reload`. Compare normalized config hashes and distinguish missing from invalid config.

- [ ] **Step 5: Implement generation-safe reconnect and explicit destruction.**

Capture the generation before reconnect. Reject publication from an old generation. Add `close_mcp_manager(user_id)` that removes the cache entry, increments the generation, cancels/awaits reconnect tasks, closes connections, and clears listeners after bridges detach.

- [ ] **Step 6: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_mcp_lifecycle.py tests/api/test_mcp_health.py tests/sdk/test_mcp.py
uv run ruff check src/sdk/tools_core/mcp_manager.py src/sdk/tools_core/mcp_config.py src/http/routers/mcp.py tests/sdk/test_mcp_lifecycle.py tests/api/test_mcp_health.py
git add src/sdk/tools_core/mcp_manager.py src/sdk/tools_core/mcp_config.py src/http/routers/mcp.py tests/sdk/test_mcp_lifecycle.py tests/api/test_mcp_health.py
git commit -m "fix: harden MCP manager lifecycle"
```

---

## Task 2: Add durable metadata cache and provenance

**Files:**
- Create: `src/sdk/tools_core/mcp_cache.py`
- Modify: `src/sdk/tools_core/mcp_config.py`
- Modify: `src/config/settings.py`
- Test: `tests/sdk/test_mcp_cache.py`
- Test: `tests/unit/test_mcp.py`

**Interfaces:**
- Produces `MCPToolMetadataCache` with `get`, `put`, `invalidate_server`, `invalidate_all`, and `status`.
- Produces server source metadata fields: `source_path`, `source_type`, `config_hash`, `disabled`.
- Adds `MCPConfig.exposure`, `direct_tools`, `cache_ttl_seconds`, `refresh_timeout_seconds`, and bounded output settings.

- [ ] **Step 1: Write failing cache tests.**

```python
def test_cache_round_trip_omits_secrets(tmp_path):
    cache = MCPToolMetadataCache(tmp_path / "mcp-cache.json")
    cache.put(server_record(headers={"Authorization": "Bearer secret"}))
    raw = (tmp_path / "mcp-cache.json").read_text()
    assert "Bearer secret" not in raw


def test_config_hash_invalidates_only_changed_server(tmp_path):
    cache = MCPToolMetadataCache(tmp_path / "mcp-cache.json")
    cache.put(server_record(name="github", config_hash="a"))
    cache.put(server_record(name="files", config_hash="b"))
    cache.invalidate_changed({"github": "new", "files": "b"})
    assert cache.contains("github") is False
    assert cache.contains("files") is True
```

- [ ] **Step 2: Run cache tests red.**

```bash
uv run pytest -q tests/sdk/test_mcp_cache.py tests/unit/test_mcp.py
```

- [ ] **Step 3: Implement the cache file and secret-free schema.**

Use a versioned JSON file under the user data root. Store only server/tool metadata, source identity, hashes, timestamps, and safe status. Reject or redact secret-bearing values before serialization.

- [ ] **Step 4: Add configuration provenance.**

Add source metadata to effective server definitions. Keep runtime/session configuration isolated and do not write them to project files. Surface conflicts in status rather than silently choosing a definition.

- [ ] **Step 5: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_mcp_cache.py tests/unit/test_mcp.py tests/api/test_mcp_health.py
uv run ruff check src/sdk/tools_core/mcp_cache.py src/sdk/tools_core/mcp_config.py src/config/settings.py tests/sdk/test_mcp_cache.py tests/unit/test_mcp.py
git add src/sdk/tools_core/mcp_cache.py src/sdk/tools_core/mcp_config.py src/config/settings.py tests/sdk/test_mcp_cache.py tests/unit/test_mcp.py
git commit -m "feat: cache MCP metadata with provenance"
```

---

## Task 3: Add the governed MCP proxy tool

**Files:**
- Create: `src/sdk/tools_core/mcp_proxy.py`
- Modify: `src/sdk/tools_core/mcp.py`
- Modify: `src/sdk/tools_core/mcp_bridge.py`
- Test: `tests/sdk/test_mcp_proxy.py`

**Interfaces:**
- Produces `mcp` `ToolDefinition` with `action`, `server`, `query`, `tool`, `args`, `limit`, and `offset`.
- Produces `MCPToolBridge.proxy_search()`, `proxy_describe()`, and `proxy_call()` methods.
- Uses `ToolResult` and the existing execution/receipt contracts.

- [ ] **Step 1: Write failing proxy tests.**

```python
async def test_proxy_search_uses_cache_without_connecting(manager):
    manager._cache.put(server_record(tools=[tool("search_me")]))
    result = await mcp_proxy.ainvoke({
        "user_id": "alice", "action": "search", "query": "search", "args": {}
    })
    assert "search_me" in result.content
    assert manager._create_connection.await_count == 0


async def test_proxy_call_revalidates_permission(manager, monkeypatch):
    monkeypatch.setattr(manager, "resolve_permission", lambda name, args: "deny")
    result = await mcp_proxy.ainvoke({
        "user_id": "alice", "action": "call", "tool": "mcp__demo__write", "args": {}
    })
    assert result.is_error is True
    assert "denied" in result.content.lower()
```

- [ ] **Step 2: Run proxy tests red.**

```bash
uv run pytest -q tests/sdk/test_mcp_proxy.py
```

- [ ] **Step 3: Implement search/describe from cached metadata.**

Search and describe must not open a connection on a cache hit. Return structured source/freshness data and bounded pagination.

- [ ] **Step 4: Implement proxy call through the bridge.**

Resolve the namespaced tool, revalidate current capability/approval policy, ensure the connection lazily, execute through the existing MCP session, and return the existing typed outcome/receipt result.

- [ ] **Step 5: Keep legacy meta-tools.**

`mcp_list`, `mcp_tools`, and `mcp_reload` remain available during migration. The proxy is additive until exposure mode is enabled.

- [ ] **Step 6: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_mcp_proxy.py tests/sdk/test_mcp_bridge.py tests/sdk/test_mcp.py
uv run ruff check src/sdk/tools_core/mcp_proxy.py src/sdk/tools_core/mcp.py src/sdk/tools_core/mcp_bridge.py tests/sdk/test_mcp_proxy.py
git add src/sdk/tools_core/mcp_proxy.py src/sdk/tools_core/mcp.py src/sdk/tools_core/mcp_bridge.py tests/sdk/test_mcp_proxy.py
git commit -m "feat: add governed MCP proxy surface"
```

---

## Task 4: Add direct-tool promotion and stale removal

**Files:**
- Modify: `src/sdk/tools_core/mcp_bridge.py`
- Modify: `src/sdk/runner.py`
- Modify: `src/config/settings.py`
- Test: `tests/sdk/test_mcp_bridge.py`
- Test: `tests/sdk/test_runner.py`

**Interfaces:**
- `MCPConfig.direct_tools: list[str]`.
- `MCPManager`/bridge exposes `sync_direct_tools()` returning added/removed names.
- `refresh_user_tool_registries()` receives only direct-tool changes.

- [ ] **Step 1: Write failing promotion tests.**

```python
async def test_hybrid_mode_registers_only_selected_direct_tools(manager):
    manager._cache.put_many([server_record(name="github"), server_record(name="files")])
    result = await bridge.sync_direct_tools(direct_tools=["mcp__github__create_issue"])
    assert result == {"added": ["mcp__github__create_issue"], "removed": []}


async def test_refresh_removes_stale_promoted_tools(bridge):
    bridge.register_direct("mcp__github__old")
    await bridge.sync_direct_tools(direct_tools=[])
    assert bridge.registry.get("mcp__github__old") is None
```

- [ ] **Step 2: Run promotion tests red.**

```bash
uv run pytest -q tests/sdk/test_mcp_bridge.py tests/sdk/test_runner.py
```

- [ ] **Step 3: Implement explicit direct-tool matching.**

Match original, namespaced, and configured direct tool names consistently. Reject ambiguous matches and report them in status.

- [ ] **Step 4: Implement stale removal and loop refresh.**

Remove definitions no longer promoted before adding current definitions. Refresh all affected live loops through the existing registry refresh path.

- [ ] **Step 5: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_mcp_bridge.py tests/sdk/test_runner.py tests/sdk/test_mcp_proxy.py
uv run ruff check src/sdk/tools_core/mcp_bridge.py src/sdk/runner.py src/config/settings.py tests/sdk/test_mcp_bridge.py tests/sdk/test_runner.py
git add src/sdk/tools_core/mcp_bridge.py src/sdk/runner.py src/config/settings.py tests/sdk/test_mcp_bridge.py tests/sdk/test_runner.py
git commit -m "feat: support explicit MCP direct tools"
```

---

## Task 5: Add MCP output guards and status snapshots

**Files:**
- Modify: `src/sdk/tool_results.py` or create `src/sdk/tools_core/mcp_results.py`
- Modify: `src/http/routers/mcp.py`
- Modify: `src/sdk/tools_core/mcp_manager.py`
- Test: `tests/sdk/test_mcp_proxy.py`
- Test: `tests/api/test_mcp_health.py`

**Interfaces:**
- Produces `guard_mcp_result(result, limits, user_id, workspace_id) -> ToolResult`.
- Status response includes `cached`, `direct_tool_count`, `source_path`, `config_hash`, and totals while preserving existing fields.

- [ ] **Step 1: Write failing output/status tests.**

```python
async def test_large_proxy_result_is_bounded_and_spilled(manager):
    result = await guard_mcp_result(large_result(), limits(max_chars=100), "alice", "personal")
    assert result.structured_content["omitted"] is True
    assert result.structured_content["result_id"]
    assert "tool_result_read" in result.content


def test_health_snapshot_is_secret_free(client):
    response = client.get("/v1/mcp/health", params={"user_id": "alice"})
    assert response.status_code == 200
    assert "Authorization" not in response.text
    assert "api_key" not in response.text.lower()
```

- [ ] **Step 2: Run output/status tests red.**

```bash
uv run pytest -q tests/sdk/test_mcp_proxy.py tests/api/test_mcp_health.py
```

- [ ] **Step 3: Implement bounded result handling.**

Bound text and structured content independently. Preserve safe previews/counts and use the existing protected result store for spill/retrieval. Never report omitted content as complete.

- [ ] **Step 4: Implement stable status snapshots.**

Status reads must not start a server, trigger OAuth, mutate registries, or reveal secrets. Emit snapshots after initialization, refresh, direct-tool reconciliation, and shutdown.

- [ ] **Step 5: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_mcp_proxy.py tests/api/test_mcp_health.py tests/sdk/test_mcp_lifecycle.py
uv run ruff check src/sdk/tool_results.py src/http/routers/mcp.py src/sdk/tools_core/mcp_manager.py tests/sdk/test_mcp_proxy.py tests/api/test_mcp_health.py
git add src/sdk/tool_results.py src/http/routers/mcp.py src/sdk/tools_core/mcp_manager.py tests/sdk/test_mcp_proxy.py tests/api/test_mcp_health.py
git commit -m "feat: bound MCP results and publish status snapshots"
```

---

## Task 6: Integration and migration verification

**Files:**
- Modify: `tests/sdk/test_mcp_lifecycle.py`
- Modify: `tests/sdk/test_mcp_proxy.py`
- Modify: `tests/api/test_mcp_health.py`
- Modify: `CHANGELOG.md` at release time

- [ ] **Step 1: Add a local stdio fixture.**

Create a test-owned stdio MCP server that exposes a changing tool catalog, can fail discovery, and can be stopped/restarted. Use bounded timeouts and no network dependency.

- [ ] **Step 2: Test cache-only search after shutdown.**

Assert search/describe succeed from cache, proxy call reconnects, and direct promotion reflects the refreshed catalog.

- [ ] **Step 3: Test policy and outcome parity.**

Call the same MCP tool through proxy and direct modes and assert identical `ToolResult` outcome/receipt semantics.

- [ ] **Step 4: Test configuration precedence and invalidation.**

Change one server definition and assert only its cache/direct registrations invalidate. Remove the file and assert status reports missing without stale direct tools.

- [ ] **Step 5: Test two live loops.**

Create two loops sharing the manager, reload one server, and assert both registries receive the same added/removed direct-tool delta.

- [ ] **Step 6: Run complete verification.**

```bash
uv run pytest -q tests/sdk/test_mcp.py tests/sdk/test_mcp_bridge.py tests/sdk/test_mcp_lifecycle.py tests/sdk/test_mcp_proxy.py tests/api/test_mcp_health.py
uv run ruff check src/
uv run mypy src/sdk/tools_core/mcp_manager.py src/sdk/tools_core/mcp_bridge.py src/sdk/tools_core/mcp_proxy.py src/http/routers/mcp.py
```

- [ ] **Step 7: Commit the integration gate.**

```bash
git add tests/sdk/test_mcp_lifecycle.py tests/sdk/test_mcp_proxy.py tests/api/test_mcp_health.py CHANGELOG.md
git commit -m "test: verify MCP proxy lifecycle integration"
```

## Final verification

- [ ] Run the full Python suite.
- [ ] Run `cd native-sdk-experiment && uv run native test`.
- [ ] Confirm proxy mode remains opt-in until migration metrics are collected.
- [ ] Confirm transport-security allowlists and OAuth remain deferred.
- [ ] Update GitHub issue #7 only with the separately completed scope.
