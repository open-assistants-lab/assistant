# EA Deployment Architecture

## Overview

Three deployment tiers, one container image:

```
Solo Desktop               Shared Host                    Dedicated VM
─────────────              ───────────                    ────────────
$0 (your Mac)              $20/user/mo                    $80/user/mo
                            
1 AgentLoop process         1 container/user              1 VM/user
all tools locally           all tools in container        all tools in container
                            
~/Assistant/     data/users/{id}/              data/users/{id}/
data/users/default_user/   on host volume                on VM disk
                            
auth: none                 auth: JWT                      auth: JWT + SSO
isolation: macOS           isolation: container           isolation: VM
                            
Same codebase, different orchestration.
```

## Standard Tier: Per-User Containers on Shared Host

```
╔════════════════════ Server ═════════════════════╗
║                                                  ║
║  ┌─ Alice Container ──────────────────────────┐  ║     Alice's Mac
║  │  AgentLoop                                 │  ║     ┌─────────────┐
║  │  firecrawl / gws / agent-browser           │  ║     │ Flutter app │
║  │  shell_execute (sandboxed)                 │  ║──WS─│ thin client │
║  │  HybridDB (SQLite + ChromaDB)              │  ║     └─────────────┘
║  │  skills / subagents                        │  ║
║  │  data/users/alice/                          │  ║     Alice's iPhone
║  │  ├── workspace/    (canonical files)        │  ║     ┌─────────────┐
║  │  ├── conversation/                          │  ║──WS─│ Flutter app │
║  │  ├── memory/                                │  ║     └─────────────┘
║  │  ├── contacts/                              │  ║
║  │  ├── email/                                 │  ║
║  │  └── todos/                                 │  ║
║  └─────────────────────────────────────────────┘  ║
║                                                    ║
║  ┌─ Bob Container ──────────────────────────────┐  ║     Bob's Mac
║  │  ...same, data/users/bob/                     │  ║     ┌─────────────┐
║  └───────────────────────────────────────────────┘  ║──WS─│ Flutter app │
║                                                    ║     └─────────────┘
║  ┌─ Gateway (thin FastAPI) ──────────────────────┐  ║
║  │  Auth (JWT)                                   │  ║
║  │  WebSocket routing (client ↔ container)       │  ║
║  │  Container lifecycle (spawn, idle-kill)       │  ║
║  │  Team data proxy                              │  ║
║  └───────────────────────────────────────────────┘  ║
╚══════════════════════════════════════════════════════╝
```

Each container is fully self-contained. The gateway is a thin FastAPI process — auth + routing + container lifecycle. No agent code runs in the gateway.

### Container spec

| Resource | Idle | Active (LLM call + tools) | Active + Browser |
|----------|------|---------------------------|-------------------|
| RAM | ~500MB | ~1.5GB | ~2.2GB (Chromium headless adds ~350MB per session; 20 concurrent users × browser adds ~7GB host overhead) |
| CPU | 0 | 2 vCPU | 2-3 vCPU |
| Disk | ~2GB (HybridDB + workspace) | Grows with usage | — |

### Host economics

At ~15% concurrent usage (chat 2 min, idle 30 min). All instances below are Hetzner **cloud** (CX series), not dedicated — cloud instances scale dynamically.

| Host | vCPU | RAM | Cost/mo | Users @ idle | Concurrent |
|------|------|-----|---------|-------------|------------|
| Hetzner CX42 | 8 | 16GB | $16 | 32 | ~10 |
| Hetzner CX52 | 16 | 32GB | $33 | 64 | ~20 |
| Hetzner CX62 | 32 | 64GB | $65 | 128 | ~40 |

At $20/user/mo, infrastructure cost is negligible (96-98% margin). The real cost is LLM API calls and support.

### Gateway Architecture

The gateway is the most architecturally significant component in Shared Host mode. It must handle:

1. **Auth (JWT)** — Decode `Authorization: Bearer <token>`, extract `user_id` and `team_id`, validate expiry and signature.
2. **Container routing** — Maintain a session map of `user_id → container_address`. On WebSocket connect, lookup or spawn the user's container, then proxy the upgrade to the container's WebSocket port.
3. **Container lifecycle** — Spawn Docker containers on demand, health-check them, terminate idle containers after timeout.
4. **Team data proxy** — Serve team-level contacts/skills/memory to members' containers (read-only) and route admin writes to the persistence layer.

**Session routing** is the non-trivial piece. The Flutter client opens a WebSocket to the gateway. The gateway must connect that stream to the correct container and hold the session for its lifetime. A reconnect (client disconnects and reconnects) must reach the **same** container — otherwise in-flight streaming state and pending HITL interrupts are lost.

Two approaches for session affinity:

| Approach | How it works | Tradeoffs |
|----------|-------------|-----------|
| **Sticky routing via LB** | Load balancer hashes `user_id` to a specific gateway replica. Gateway holds session map in memory. | Simple for single host. Breaks when adding hosts unless LB layer supports consistent hashing. Single gateway replica becomes hot spot for active users on its hash bucket. |
| **External session store** | Gateway replicas are stateless. Session map lives in Redis or a SQLite file on a shared volume. Any replica can serve any user. | Scales horizontally without sticky sessions. Adds Redis dependency. Session state is small (user_id → container IP:port) — Redis is lightweight. |

**Recommended approach**: External session store (Redis or shared SQLite) from day one. It avoids coupling scaling to sticky routing and keeps the gateway stateless. The container routing lookup is a single key read on each WebSocket connect.

**Container spawn path:**
```
1. Client opens WS to gateway (wss://ea.example.com/ws)
2. Gateway decodes JWT → user_id = "alice@corp.com"
3. Gateway checks session store: user_id → ?
4. If no entry:
   a. Spawn Docker container: docker run --volume data/users/alice:/data ea-image
   b. Wait for health check (HTTP GET /health on container)
   c. Write session store: user_id → 172.17.0.X:8080
5. Gateway opens WS to container's ws://172.17.0.X:8080/ws/conversation
6. Gateway bridges: client ↔ container, passing through messages unchanged
7. On disconnect: gateway closes container WS. Session persists until idle timeout.
```

**Container kill path:**
```
1. Idle timer fires (15 min since last WS message) → gateway sends SIGTERM to container
2. Container drains active AgentLoop operations (5s grace period)
3. Container killed. Session store entry removed.
4. Data survives on host volume. Next connect spawns fresh container with same data mount.
```

**Failure modes:**

| Failure | Gateway behavior |
|---------|-----------------|
| Container OOM | Gateway detects closed WS, removes session, returns 500 to client. Next connect spawns fresh container. |
| Container won't start | Gateway retries once, then returns 503 to client with retry-after header. |
| Gateway crashes | Container runs until idle timeout kills it. Client reconnects to another gateway replica (with session store, finds existing container). |
| Host out of RAM | New spawn fails. Gateway returns 507 with message. Admin alert triggers. |

**Multiple gateway replicas** run behind Traefik or nginx. With the external session store, the load balancer doesn't need sticky sessions — any replica can serve any user. Auth state lives in the JWT (no server-side auth session).

### Gateway implementation effort

The gateway is a new FastAPI service, separate from the agent codebase. Estimated 5-7 days for MVP:

- JWT middleware (1 day)
- Docker client integration + container spawn/kill (2 days)
- WebSocket bridging + session store (2 days)
- Health checks + error handling + logs (1 day)
- docker-compose orchestration for dev/test (1 day)

This is Phase 15 in the roadmap.

### Scaling beyond one host

When a single host hits capacity (RAM or concurrent CPU), add hosts. The gateway becomes multi-host:

1. Session store maps `user_id → {host_id, container_address}`. On reconnect, gateway routes to the correct host.
2. New user allocation checks host capacity before spawning. A simple resource tracker (or Docker Swarm / Nomad) picks the least-loaded host.
3. Multiple gateway replicas behind a load balancer. No sticky sessions needed.

### Idle timeout rationale

Containers idle at ~500MB RAM. At 15-minute timeout with 100 users at ~15% concurrency, ~85 containers sit idle = 42GB RAM on the host. A 5-minute timeout cuts this to ~28GB. The tradeoff is cold start latency on reconnect. 15 minutes is chosen as a reasonable balance — it covers short breaks (coffee, meeting) without holding excessive RAM. This is configurable per deployment.

## Premium Tier: Dedicated VM per User

```
Alice's VM (CX32: 4 vCPU, 8GB)     Bob's VM (CX32: 4 vCPU, 8GB)
┌──────────────────────────────┐   ┌──────────────────────────────┐
│  AgentLoop                   │   │  AgentLoop                   │
│  All CLIs                    │   │  All CLIs                    │
│  HybridDB                    │   │  HybridDB                    │
│  data/users/alice/           │   │  data/users/bob/             │
└──────────────────────────────┘   └──────────────────────────────┘
```

Same container image. Different host. The VM provides:
- Guaranteed CPU/RAM (no noisy neighbors)
- Full OS-level isolation (SOC2/HIPAA auditors happy)
- Custom firewall rules per user
- Persistent processes (no idle-kill; the VM runs 24/7)

Pricing: Hetzner CX32 at $8/user/mo = 60% margin at $20/user. AWS/GCP dedicated instances = $20-40/user = break-even or loss. Premium tier should be $80-200/user on cloud providers.

### Local LLM inference (future)

For users who want on-device inference, the Premium tier can run llama.cpp or vLLM inside the VM. This requires:

- Model weights stored on the VM disk (10-70GB per model depending on quantization)
- Dedicated GPU or sufficient CPU RAM (8-16GB for 7B quantized models)
- Integration with the existing provider abstraction (add a new `LocalProvider` that wraps the inference engine API)
- Separate resource budgeting — model RAM + Chromium + HybridDB must fit within the VM allocation

This is not implemented and is a long-term Premium feature. Phase 19+.

## Solo Desktop (Current)

```
Your Mac
┌──────────────────────────────┐
│  EA Server (one process)     │
│  All CLIs locally            │
│  HybridDB locally            │
│  ~/Assistant/      │
│  data/users/default_user/    │
│                              │
│  Auth: none (localhost)      │
└──────────────────────────────┘
        │
        ├──WS── Flutter (macOS)
        ├──WS── Flutter (iPhone, iPad) ← LAN/Tailscale
        └──WS── Flutter (web)
```

Works today. Zero config. The codebase is already built for this.

The server is started with `assistant http` — host is default-bind `0.0.0.0`, port is configurable via `API_PORT` env var (default `8000`, not `8080`). See `src/config/settings.py` `ApiConfig`.

## Dockerfile

> **Note:** The Dockerfile below is the **target** spec. The current `docker/Dockerfile` is a minimal dev build (Python 3.13, `uv sync`, no tool CLIs, no Chromium). That file works for local testing but does not include the full tooling for production. We'll update it to match this spec in Phase 13.

```dockerfile
FROM python:3.11-slim

# System deps for Chromium, Playwright, firecrawl
RUN apt-get update && apt-get install -y \
    chromium chromium-driver \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv pip install --system -e ".[http]"

# CLI tools
RUN npm install -g firecrawl@latest
RUN pip install agent-browser gws-cli

# App
COPY src/ src/
COPY config.yaml .

# Data volume (mounted from host: data/users/{user_id}/ → /data)
VOLUME /data

ENV API_HOST=0.0.0.0
ENV API_PORT=8080

CMD ["ea", "http"]
```

Per-user containers mount `data/users/{user_id}/` at `/data` via Docker volumes. The application resolves all data paths relative to `/data` via the `DATA_DIR` config, which defaults to `data/` locally but should be set to `/data/` in the container environment.

## Data Paths (Unified Across All Tiers)

```
data/
├── users/
│   ├── default_user/              # Solo mode
│   │   ├── conversation/
│   │   ├── memory/
│   │   ├── email/
│   │   ├── contacts/
│   │   ├── todos/
│   │   ├── workspace/
│   │   │   └── .versions/
│   │   ├── skills/
│   │   ├── subagents/
│   │   │   ├── agent_defs/
│   │   │   └── work_queue.db
│   │   └── .mcp.json
│   ├── alice@corp.com/            # Multi-tenant
│   │   └── (same structure)
│   └── bob@corp.com/
│       └── (same structure)
├── teams/
│   └── engineering/
│       ├── contacts/
│       ├── skills/
│       ├── memory/
│       ├── files/
│       └── config.yaml
├── cache/
│   └── models.json
├── logs/
└── traces/
```

Solo: `data/users/default_user/`. Multi-tenant: `data/users/{user_id}/`. Same path logic, just the user_id changes. Team data at `data/teams/{team_id}/` only exists in multi-tenant mode.

## Where Each Tool Runs

| Tool | Solo | Container/VM |
|------|------|-------------|
| `files_*` | `~/Assistant/` | `data/users/{user_id}/workspace/` |
| `shell_execute` | Native terminal | Sandboxed in container |
| `firecrawl` | Local CLI | CLI in container |
| `gws` (email/contacts/todos) | Local CLI | CLI in container |
| `agent-browser` | Local Chromium | Headless Chromium in container |
| `memory_*` | HybridDB locally | HybridDB in container |
| `time_get` | Pure Python | Pure Python |

Everything runs inside the container. Thin clients connect via WebSocket only.

### MCP tools

The codebase already includes an MCP tool bridge (`src/sdk/tools_core/mcp.py`, `mcp_bridge.py`, `mcp_manager.py`) that dynamically discovers and registers tools from MCP servers. In containerized deployments, MCP servers run as sidecar processes inside the container or as separate containers on the same Docker network. MCP tools appear to the agent as `mcp__{server}__{tool}` alongside native SDK tools. No changes to the tool routing table above — MCP tools execute inside the same container as the AgentLoop.

## Auth

| Tier | Auth Method | Identity |
|------|------------|----------|
| Solo Desktop | None (localhost) or API key (LAN) | `user_id="default_user"` |
| Shared Host | JWT | `user_id` + `team_id` from token |
| Dedicated VM | JWT + SSO (SAML/OIDC) | Same |

Server tries JWT decode first, falls back to API key. One `Authorization: Bearer <token>` header.

Note: API key auth is not yet implemented. Phase 12 adds it for LAN/Tailscale access from mobile clients.

## Per-User Isolation

| Tier | Mechanism | Bug Risk |
|------|-----------|----------|
| Solo | None needed | N/A |
| Shared Host | Container + per-user volume mount | Container breakout = data leak |
| Dedicated VM | VM hypervisor | VM escape = nation-state level only |

SQLite file-level locking is a non-issue: Alice's container writes `alice/emails.db`, Bob's writes `bob/emails.db`. Different files, zero contention.

## Team Data

Team data at `data/teams/{team_id}/` is mounted **read-only** into each member's container. Members can read team contacts, skills, memory, and files but cannot write to them through their agent.

Admin writes go through a separate admin service (or CLI on the host). This is the single writer. Team members pick up changes on their next tool call (the read-only mount reflects the admin's writes automatically).

WAL mode in SQLite allows concurrent readers + 1 writer — the admin service's writes never block member reads.

Agent-initiated writes to team data (e.g., the agent wants to add a team contact during a conversation) are not supported in the read-only model. Workarounds:
- Agent prompts the user to forward to an admin
- Agent writes to the user's personal data and raises a suggestion flag
- A future "suggested edits" queue could let admins review and approve agent-proposed team changes

## Container Lifecycle

```
User connects    →    Gateway spawns container if not running
User idle 15 min →    Gateway sends SIGTERM → container drains → killed
User reconnects  →    Gateway spawns fresh container (data persisted on volume)
```

Containers are ephemeral. Data persists on the host volume. A container crash = AgentLoop restart with state recovery from HybridDB.

## What's Already Done

1. ✅ `DataPaths` — `data/users/{user_id}/` path structure
2. ✅ `DEFAULT_USER_ID = "default_user"` for solo mode (defined in `src/storage/paths.py`)
3. ✅ Per-user SQLite + ChromaDB (HybridDB)
4. ✅ All tools SDK-native (~93 tools including MCP bridge)
5. ✅ `files_*` default workspace: `~/Assistant/` (solo)
6. ✅ WebSocket + HITL + streaming
7. ✅ Flutter app (thin client on macOS, iOS, iPadOS)

## What's Needed for Tiers

| Phase | What | Tier | Effort |
|-------|------|------|--------|
| Now | Solo desktop (current) | Solo | ✅ Done |
| 12 | API key auth for LAN/Tailscale | Solo | 2-3 days |
| 13 | Production Dockerfile (Chromium + CLI tools) + docker-compose | All | 3-4 days |
| 14 | JWT auth | Shared | 3 days |
| 15 | Gateway (auth, container routing, session store, lifecycle) | Shared | 5-7 days |
| 16 | Team data isolation (read-only mounts) | Shared | 2 days |
| 17 | SSO / SAML | Premium | 5 days |
| 18 | Admin service (provisioning, billing) | Premium | 10-15 days |
| 19 | Local LLM inference (llama.cpp / vLLM) | Premium | TBD |
