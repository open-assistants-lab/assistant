# Assistant — Deployment Guide

This guide describes deployment topologies and operational basics. Not every
isolation design below is implemented. For application packaging, ownership and
adoption, start with [builder/deployer conventions](docs/builder-deployment-guide.md).
Use dedicated runtime/storage/credential boundaries for unrelated customers;
a shared process is a trusted-user arrangement, not hostile-tenant isolation.

- **Mode 1 — Local**: one user, one machine, terminal or desktop app.
- **Mode 2 — Solo WAN**: one user, many devices. Sessions (and files) stay in
  sync because there is exactly one server.
- **Mode 3a — Multi-user, trusted**: several trusted users in **one container**.
  The shared server currently uses a shared uid. Per-tenant worker/OS-user
  isolation is a design target, **not built**, see [Status](#status).
- **Mode 3b — Dedicated runtime**: **one container (or microVM) per user or
  customer boundary**, with separately configured authentication. Container/VM
  isolation still depends on host configuration, mounts, privileges and egress.

> **Docker recipes and legacy scripts:** [`docker/DEPLOYMENT.md`](docker/DEPLOYMENT.md).
> Read those alongside this guide's identity/isolation limits and the builder
> adoption checklist; not every recipe meets the wider contract unchanged. **No clone needed:** `docker pull
> ghcr.io/open-assistants-lab/assistant:latest` — published on every release tag
> (multi-arch amd64+arm64).

## Isolation by mode

Isolation is a property of the **topology**. 3a and 3b differ in *how many
users can share a boundary*, not in how strong the authentication is.

| Deployment | Containers | OS users | Sandbox | Auth | **What separates users** |
|---|---|---|---|---|---|
| 1 — Local | none | 1 | soft | none | the user's own OS account |
| 2 — Solo WAN | 1 | 1 | soft | shared `API_KEY` | the user's OS account |
| **3a — trusted** | **1** | **shared uid today** | soft | shared key or configured scoped identity | **API/tool ownership checks, not a hostile-user OS boundary** |
| 3b — dedicated | one per user/customer boundary | configured per runtime | operator-configured | deployment key or scoped identity | the configured container / VM |

In 3a per-user paths and identity-aware API/tool checks are not separate OS
accounts. Arbitrary code with equivalent service credentials remains a distinct
authority path. In 3b the configured container/VM provides an additional boundary;
shared mounts, privileged access or exposed backend ports can undermine it.

### Status

| | |
|---|---|
| **Per-tenant worker/OS user (real 3a isolation)** | **not built for the shared API server.** Per-user paths and sandbox subprocess uid handling do not provide independent server workers, store ownership or service credentials per tenant. |
| Detection + `shell_execute` cap | built. Fires only when one process serves several users. |
| Mode 3b (dedicated container) | Existing recipe/generator; startup and isolation must be validated in the chosen environment. |

Building 3a properly needs a **per-tenant worker process**, so the uid is dropped
at process creation. That is not optional polish: `setuid` per request inside an
async server handling every tenant is unsafe — one missed switch is a full
compromise, and it is not safe under concurrency.

The shell cap is a stopgap, not complete isolation. Identity-aware API checks and
filesystem-tool ownership checks also exist, but do not contain arbitrary code
with the runtime's credentials. The server records which users it has
served; once one process has served more than one, an `allow` for
`shell_execute` resolves to `ask`. If your isolation comes from outside the
process entirely (a VM, a separate host), set
`governance.allow_shell_when_multi_user: true` — deliberately and audibly.

The cap narrows shell approval policy; it does not provide per-tenant workers,
separate service credentials or hostile-user containment.

## Architecture in 30 seconds

- The **server is the single source of truth**: conversation history, memory,
  email, todos, contacts, files all live server-side in per-user stores.
- **Clients are thin viewers** — they pull history and stream events
  (REST/SSE/WebSocket). Multi-device sync is a property of having one server,
  not of any client-side sync engine.
- **User data is namespaced per user** under `data_root` (default `~/Assistant/`),
  e.g. `~/Assistant/Messages/messages.db`, `~/Assistant/Files/`,
  `~/Assistant/Memory/`. Non-default users are under `Users/{user_id}/`.
  Settings/vaults and project state also use `data_path` (default `data/`);
  inventory and back up both trees. Namespaces alone are not OS isolation.
- **One process per user store.** The server keeps per-user in-memory state
  (message store caches, agent loops, session registry) and SQLite/ChromaDB
  are single-writer per user. Do **not** run multiple replicas serving the
  same user's data — horizontal scaling per user is not supported.

---

## Choosing a deployment

| Scenario | Use | Notes |
|---|---|---|
| Single user, local API clients | **Mode 1 — Local** | Explicit loopback binding; choose your model. Other local apps may access local files. |
| Single user, multiple devices (phone, laptop, desktop) | **Mode 2 — Solo WAN** | One server = sessions **and** files in sync everywhere. |
| Several trusted users sharing one host | **Mode 3a — trusted** | Shared uid; configure actor identity. No hostile-tenant isolation — see [Status](#status). |
| Separate user/customer boundaries with configured auth | **Mode 3b — dedicated** | Existing container recipe/generator; validate host, mounts, privileges and identity. |
| Enterprise teams (SSO, shared workspaces) | **Requires application/deployment review** | Browser OIDC exists; it does not establish a complete organisation-sharing or hostile-tenant contract. See [Known gaps](#known-gaps-as-of-this-document). |

---

## Native tool policy

Control shipped native tools at deployment scope with `tools.native` in
`config.yaml`:

```yaml
tools:
  native:
    mode: selected       # all | selected | none
    enabled:
      - files_read       # exact names and case-sensitive glob patterns
      - files_glob_*
```

- `all` (the default) exposes all shipped native tools permitted by the active deployment profile; for example, desktop-server still excludes its desktop-incompatible families.
- `selected` exposes only native names matching `enabled` patterns.
- `none` exposes no shipped native tools, including shipped meta-tools.
- Environment overrides are `TOOLS_NATIVE__MODE` and
  `TOOLS_NATIVE__ENABLED`; the latter is a JSON list, for example
  `TOOLS_NATIVE__ENABLED='["files_read","files_glob_*"]'`. Invalid mode or
  list values fail configuration validation rather than falling back to
  `all`.
- The policy is a hard deployment ceiling. User/workspace capabilities can
  further restrict native tools but cannot restore a native tool excluded by
  this policy.

Custom tools remain separate: each custom tool is defined by its own
`TOOL.md`, which deployments may add, edit, or remove. MCP-provided tools are
also separate from this native policy. A future generated `TOOLS.md` catalog,
if added, will be informational only and not an authorization source.

---

## Optional dependency: agent-browser CLI (browser automation)

Interactive browser tools (`browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_eval`) and the `web-automation` skill's long-tail commands drive the **`agent-browser` CLI** (Vercel Labs, Rust binary, Chrome/Chromium via CDP). Without it, browser tools return an install hint and the agent falls back to zero-config `web_fetch`/`web_search` — only interactive browsing is unavailable.

- **Mode 1 (Local)**: `brew install agent-browser` (macOS) or `npm i -g agent-browser && agent-browser install`. The desktop app may offer one-click install in future.
- **Mode 2 (Solo WAN)**: install on the server (same commands).
- **Mode 3 (Multi-tenant)**: not yet in the Docker image — browser tools are effectively unavailable in containers today. Either add `agent-browser` + Chromium to the image, or disable browser tools per user via capabilities.

The CLI must be listed in `shell_tool.allowed_commands` in `config.yaml` (already included by default) for the `web-automation` skill's `shell_execute` commands to run. The shell sandbox rejects metacharacters, so each browser command is a single `shell_execute` call (no chaining).

---

## Mode 1 — Local

One user, one machine, explicitly bound to loopback. Choose your model/provider.
The unmodified host default is `0.0.0.0`, not localhost-only.

```bash
API_HOST=127.0.0.1 uv run assistant http
```

- **URL**: `http://localhost:8080`
- **Auth**: disabled (localhost-only, no API key needed)
- **Data**: user data at `~/Assistant/`, project data at `./data/`
- **Config**: `config.yaml` + `.env` (see [Secrets](#secrets))

Stop the server before copying data for a migration/backup of the file tree.

---

## Mode 2 — Solo WAN (multi-device sync)

**Use case:** the same user on desktop + phone/laptop. Because all devices
connect to *one* server, chat sessions and files are identical everywhere —
there is nothing to "sync".

### Option A: Tailscale (no port forwarding)

1. Install Tailscale on the server machine and on each device.
2. Generate an API key and start the server:

```bash
export API_KEY=$(openssl rand -hex 32)  # store securely; do not log the value
export SOLO_BYPASS=false
uv run assistant http                  # binds 0.0.0.0:8080 by default
```

3. Configure your API client for `http://<server-tailscale-ip>:8080` and the
   deployment key, within the encrypted mesh. This is not a claim of finished
   native remote-login support.

**How it works:** Tailscale provides an encrypted mesh network between your
devices. `API_KEY` gates remote connections but does not identify an individual
actor. Disable bypass on remote deployments; prevent direct backend access and
proxy-loopback bypass. Use HTTPS when exposing an endpoint outside this explicitly
trusted encrypted mesh.

### Steps B: Public VPS with Docker

1. Deploy the container image on a small VPS (see Mode 3 for the compose
   file; use a single `app` service).
2. Put Caddy (or your reverse proxy) in front for TLS.
3. Point all devices at `https://your.domain` with the same `API_KEY`.

---

## Mode 3 — Multi-tenant (Docker + reverse proxy)

**Use case:** host the assistant for several users on one machine. Each user
gets their own container with its own data volume and API key — this keeps
the agent's shell/filesystem access isolated per user at the OS level.

```
bob.myea.com   ──┐
                 ├──► Caddy (TLS, :443) ──► alice:8080  (alice's container)
alice.myea.com ──┘                          bob:8080    (bob's container)
```

### 1. DNS

Point a wildcard `*.myea.com` record at the server's IP.

### 2. API keys

```bash
openssl rand -hex 32   # alice's key
openssl rand -hex 32   # bob's key
```

### 3. Caddyfile

```caddy
*.myea.com {
    tls { dns cloudflare {env.CLOUDFLARE_API_TOKEN} }

    @alice host alice.myea.com
    handle @alice { reverse_proxy alice:8080 }

    @bob host bob.myea.com
    handle @bob { reverse_proxy bob:8080 }
}
```

### 4. docker-compose.yml

```yaml
services:
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
    environment:
      - CLOUDFLARE_API_TOKEN=${CF_TOKEN}

  alice:
    build: { context: .., dockerfile: docker/Dockerfile }
    command: ["uv", "run", "assistant", "http"]
    environment:
      - API_KEY=${ALICE_KEY}
      - SOLO_BYPASS=false
      - DEPLOYMENT_DATA_ROOT=/app/data        # user data → volume
      - DEPLOYMENT_DATA_PATH=/app/data      # project data → volume
      - API_PORT=8080
    volumes:
      - alice_data:/app/data

  bob:
    build: { context: .., dockerfile: docker/Dockerfile }
    command: ["uv", "run", "assistant", "http"]
    environment:
      - API_KEY=${BOB_KEY}
      - SOLO_BYPASS=false
      - DEPLOYMENT_DATA_ROOT=/app/data
      - DEPLOYMENT_DATA_PATH=/app/data
      - API_PORT=8080
    volumes:
      - bob_data:/app/data

volumes:
  alice_data:
  bob_data:
```

> **Important**: set `DEPLOYMENT_DATA_ROOT` — without it user data lands in
> `/root/Assistant` *inside* the container and is lost on recreation.

### 5. Start and add users

```bash
ALICE_KEY=abc123 BOB_KEY=xyz789 CF_TOKEN=... docker compose up -d
```

Each user connects to their subdomain with their API key. Adding a user =
one new service block + one Caddy entry + one volume.

---

## Verification & the grader (rubric checks)

Responses can be auto-verified against a **rubric** by a separate **grader
loop** before they reach the user. Ownership model:

- **The admin owns the grader** — model, tools, iterations, and the default
  rubric are deployment policy (`VERIFICATION_*` in `.env` / config.yaml
  `verification:`), not per-user preferences. The worker serves the user;
  the grader serves the deployment — same party owning both would be
  self-grading.
- **The grader prompt is hash-pinned**: each user's settings record the
  sha256 of the grading prompt actually used, so verification results are
  auditable ("this output passed rubric X against prompt hash Y").
- **Per-run rubrics** are allowed (pass `verification.rubric` in a request)
  and recorded in the audit trail — transparent variation.
- **Off by default** — enable with `VERIFICATION_ENABLED=true`; the grader
  model defaults to the agent model. Grader prompt is seeded per user and
  editable via the Settings API; the prompt hash is pinned when a user
  enables verification.

Kits may ship rubrics (admin-curated at install) — that is the sanctioned
per-vertical variation, not user-defined ad-hoc bars.

---

## Data layout and backups

| What | Where | Back up |
|---|---|---|
| User data (conversation, files, memory, email, todos, contacts, skills, subagents) | `data_root` (`~/Assistant/`, or `DEPLOYMENT_DATA_ROOT`) | **Yes** |
| Settings, scopes, connector vaults and project state | `data/` (`DEPLOYMENT_DATA_PATH`), including per-user settings trees | **Yes** for non-regenerable configuration/credentials/jobs; classify caches/logs separately |
| Per-user DBs | `data_root/Messages/messages.db`, `Memory/…`, `Email/emails.db`, `Contacts/contacts.db`, `Todos/todos.db`, `Subagents/work_queue.db` (under each user's root) | **Yes** |
| Vector index | `data_root/Memory/` (ChromaDB dirs) | Yes — but see below |
| File versions | `data_root/.versions/`, `data_root/Files/` | **Yes** |

The server is the single copy of truth — **backups are not optional**.

### SQLite databases (WAL-safe online backup)

```bash
sqlite3 "$DATA_ROOT/Messages/messages.db" ".backup '$BACKUP_DIR/messages-$(date +%F).db'"
```

Repeat for each `*.db` you want to protect. `.backup` is consistent even
while the server is running (WAL mode).

### ChromaDB + file tree (needs a stopped server)

Chroma's HNSW index files and the `Files/` tree are not transactionally safe
to copy live. Either:

- **Quick path**: stop the container (`docker compose stop app`), copy
  `data_root/`, start it again.
- **Continuous**: stream the SQLite DBs with [Litestream](https://litestream.io)
  (WAL-to-S3) for near-real-time DB backups, plus periodic stopped-server
  snapshots of the index + files.

### Restore

Restore consistent copies of both configured data trees into a separate test
location first. Review restored identity/credential records, keep writers and
schedules disabled, and validate before enabling work. Never automatically replay
an uncertain external action. Mixing stores from different backup points can
produce a valid but inconsistent assistant. Code rollback is not database
recovery or reversal of a business action; document each separately.

The [synthetic Jen stopped-copy recipe](examples/jen_reference/README.md#stopped-backup-and-restore)
is a worked local example, not a universal online/production backup service.

---

## Secrets

All secrets live in `.env` (see `.env.example`). Never bake them into the
image.

| Env var | Purpose |
|---|---|
| `API_KEY` | API key for non-localhost connections (multi-device / multi-tenant) |
| `SOLO_BYPASS` | `true` (default): skip auth for localhost requests |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` | LLM provider keys |
| `OLLAMA_API_KEY`, `OLLAMA_BASE_URL` | Ollama Cloud (`ollama-cloud:` provider) |
| `OLLAMA_LOCAL_BASE_URL` | Local Ollama (`ollama:` provider, OpenAI-compatible `/v1` API) |
| `FIRECRAWL_API_KEY`, `FIRECRAWL_BASE_URL` | Web search/scraping (self-hosted base URL needs no key) |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `LANGFUSE_ENVIRONMENT` | Trace/observability backend |
| `CONNECTKIT_VAULT_KEY` | Set to persist OAuth tokens (Gmail/Outlook) across restarts |
| `EMAIL_GWS_CLIENT_ID`, `EMAIL_GWS_CLIENT_SECRET`, `EMAIL_M365_CLIENT_ID` | OAuth desktop client credentials |

`OLLAMA_BASE_URL` configures the `ollama-cloud:` provider and defaults to
`https://ollama.com`; it does **not** configure the local `ollama:` provider.
For `ollama:<model>`, use `OLLAMA_LOCAL_BASE_URL` (default:
`http://localhost:11434/v1`). Inside a container, that default points to the
container itself. Docker Desktop deployments that need a host-side daemon can
use `http://host.docker.internal:11434/v1`.

On Linux, Compose `extra_hosts: ["host.docker.internal:host-gateway"]` only
maps the hostname; it **cannot** reach an Ollama daemon bound solely to
`127.0.0.1`. Either bind Ollama to a bridge-reachable host address and restrict
port 11434 to the Docker bridge with a host firewall, or run a local reverse
proxy that accepts only bridge traffic and forwards to Ollama's loopback
listener. Do not expose Ollama publicly merely to make it reachable from the
container.

## Observability

- **Health**: `GET /health` and `GET /health/ready` (unauthenticated).
- **Logs**: JSONL per day at `data/logs/YYYY-MM-DD.jsonl`
  (`LOGGING_LEVEL`, `LOGGING_JSON_DIR`).
- **Traces**: Langfuse if configured (see envs above).

### Observability (OB-0/OB-1)

Two optional, processor-isolated telemetry channels are **off unless
explicitly configured**. Semantic telemetry and operational telemetry never
share an exporter pipeline. There is **no vendor telemetry channel** — no
consent tiers, vendor endpoints, or vendor egress.

**Langfuse (agent semantics — traces, generations, tool spans):**

- `LANGFUSE_ENABLED=true` + `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`
  enable it.
- **Host is required, fail-closed**: an enabled Langfuse with credentials
  but no explicit host **refuses to start** rather than defaulting to
  `cloud.langfuse.com`. Set `LANGFUSE_BASE_URL`
  (`https://<your-langfuse-host>` — the same spelling Langfuse's own setup
  page shows); legacy `LANGFUSE_HOST` is still accepted.

**Operational OTLP export (runtime spans to your ClickStack/collector):**

- `OTEL_ENDPOINT=<full OTLP/HTTP traces URL>` — e.g.
  `https://clickstack.example.com/v1/traces`. Empty/unset = no operational
  provider, exporter, or operational spans are constructed.
- `OTEL_HEADERS=<json object>` — optional auth headers for the collector.
- Exported operational spans are **filtered**: only allowlisted runtime
  attributes (HTTP lifecycle, DB operation class, sandbox/backend,
  scheduler/background, model identity, and release identity) reach the
  destination. Prompts, completions, tool arguments/results, SQL, command
  text, request/response content, and Langfuse semantic spans do not reach
  it. Langfuse receives semantic telemetry only; a shared trace ID joins the
  isolated views.
- Export runs on a bounded background batch (5 s timeout); a slow or
  unreachable collector causes **drops, never request backpressure**.

### Governed external operations

Async tools with `annotations.executor.kind: external_http` require a deployment-held
`GOVERNANCE_OPERATION_CALLBACK_SECRET`. It derives operation-scoped callback capabilities
and must never be stored in `TOOL.md`, callbacks, or the governance database. The deployment
refuses external async approval when this secret is unset.

External dispatch also fails closed unless its URL host (or exact `host:port`) is listed in
`GOVERNANCE_EXTERNAL_EXECUTOR_ALLOWED_HOSTS` (JSON list) or
`governance.external_executor_allowed_hosts` in configuration. Its `dispatch_url` must be an
absolute HTTP(S) URL without userinfo; model arguments never choose it. Treat every allowed
host as a privileged outbound integration, restrict egress at the network layer, and never
accept an arbitrary user-supplied dispatch host. The operation callback capability is delivered
only in the immutable dispatch envelope; do not log it.

## Production hardening checklist

- [ ] Authentication configured on any server reachable beyond localhost; shared deployment key is not individual identity
- [ ] `SOLO_BYPASS=false`; proxy/backend bypass prevented; scoped actor ownership checks exercised
- [ ] TLS terminated by Caddy/ingress (never plain HTTP on a public IP)
- [ ] Per-user container/volume isolation (Mode 3)
- [ ] Run containers as non-root; rootfs read-only where possible
- [ ] Resource limits per container (memory/CPU) to protect the host
- [ ] Backups configured **and restored at least once**
- [ ] Health endpoint monitored; restart on failure

## Known gaps (as of this document)

- **Authentication is opt-in and deployment-sensitive.** Per-user keys
  (`PER_USER_AUTH=true`, `/auth/keys`) and browser OIDC (`OIDC_*`,
  `/auth/oidc/login`) exist through `IdentityResolver`. Shared `API_KEY`
  access remains trusted-deployment access, not a scoped individual identity.
  Keep that privileged key with operators/trusted intermediaries and test
  unauthorised and mismatched-user requests. Native OIDC handoff is not shipped.
- **General organisation/team sharing is not established by authentication.**
  Existing tenancy surfaces are not proof of complete membership, conversation,
  source, credential or business-approval isolation.
- **Shared-server per-tenant worker/uid isolation is not built.** Governed tools
  and API identities do not contain arbitrary code with service credentials.
- **Container-per-user does not scale past tens of users** on one host; the
  planned shape at that scale is an auth front + per-user workers, not more
  containers.
- **Offline/sync:** server-authoritative only. No client-side offline queue
  or bidirectional file sync yet (file cache with
  `cloud_only/downloaded/pinned` statuses is partially built).
- **One process per user store** — no horizontal scaling for a single user.

## Troubleshooting

- **Container won't start** → check the command is `uv run assistant http`
  (`assistant-sdk` and `ea` are not the current console script).
- **Port mismatch** → the server listens on **8080** (the canonical port).
- **Data "disappears" after container recreation** → `DEPLOYMENT_DATA_ROOT`
  must point into the mounted volume (see Mode 3).
- **Health check fails** → `curl` is not installed in the slim image; use the
  healthcheck in `docker/docker-compose.yaml` (python `urllib`).
