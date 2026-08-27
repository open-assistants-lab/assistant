# Assistant — Deployment Guide

This guide covers the three supported deployment modes and the operational
basics (data layout, backups, secrets, observability) that apply to all of them.

- **Mode 1 — Local**: one user, one machine, terminal or desktop app.
- **Mode 2 — Solo WAN**: one user, many devices. Sessions (and files) stay in
  sync because there is exactly one server.
- **Mode 3 — Multi-tenant**: one container per user behind a reverse proxy.
  The current safe path for hosting several users on one machine.

> **Docker deployment source of truth:** [`docker/DEPLOYMENT.md`](docker/DEPLOYMENT.md)
> (multi-user trusted deployment). This file covers the three deployment modes and
> host/VPS specifics.

## Architecture in 30 seconds

- The **server is the single source of truth**: conversation history, memory,
  email, todos, contacts, files all live server-side in per-user stores.
- **Clients are thin viewers** — they pull history and stream events
  (REST/SSE/WebSocket). Multi-device sync is a property of having one server,
  not of any client-side sync engine.
- **User data is isolated per user** under `data_root` (default `~/Assistant/`),
  e.g. `~/Assistant/Conversation/messages.db`, `~/Assistant/Files/`,
  `~/Assistant/Memory/`. Project data (cache, logs, jobs) lives under
  `data/`.
- **One process per user store.** The server keeps per-user in-memory state
  (message store caches, agent loops, session registry) and SQLite/ChromaDB
  are single-writer per user. Do **not** run multiple replicas serving the
  same user's data — horizontal scaling per user is not supported.

---

## Choosing a deployment

| Scenario | Use | Notes |
|---|---|---|
| Single user, desktop only | **Mode 1 — Local** | Zero configuration. Data is local files — other apps can access them directly. |
| Single user, multiple devices (phone, laptop, desktop) | **Mode 2 — Solo WAN** | One server = sessions **and** files in sync everywhere. |
| Several users on one host (family, small team) | **Mode 3 — Multi-tenant** | Container per user = OS-level isolation for the agent's shell/files access. |
| Enterprise teams (SSO, shared workspaces) | **Not available yet** | See [Known gaps](#known-gaps). The data model has team skeletons but no identity layer. |

---

## Optional dependency: agent-browser CLI (browser automation)

Interactive browser tools (`browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_eval`) and the `web-automation` skill's long-tail commands drive the **`agent-browser` CLI** (Vercel Labs, Rust binary, Chrome/Chromium via CDP). Without it, browser tools return an install hint and the agent falls back to zero-config `web_fetch`/`web_search` — only interactive browsing is unavailable.

- **Mode 1 (Local)**: `brew install agent-browser` (macOS) or `npm i -g agent-browser && agent-browser install`. The desktop app may offer one-click install in future.
- **Mode 2 (Solo WAN)**: install on the server (same commands).
- **Mode 3 (Multi-tenant)**: not yet in the Docker image — browser tools are effectively unavailable in containers today. Either add `agent-browser` + Chromium to the image, or disable browser tools per user via capabilities.

The CLI must be listed in `shell_tool.allowed_commands` in `config.yaml` (already included by default) for the `web-automation` skill's `shell_execute` commands to run. The shell sandbox rejects metacharacters, so each browser command is a single `shell_execute` call (no chaining).

---

## Mode 1 — Local

One user, one machine, `localhost` only. Zero configuration.

```bash
uv run assistant-sdk http
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
export API_KEY=$(openssl rand -hex 32)
echo "Your API key: $API_KEY"   # save this!
uv run assistant-sdk http              # binds 0.0.0.0:8080 by default
```

3. On each client device: Settings → Connection → Host
   `http://<server-tailscale-ip>:8080`, enter the API key.

**How it works:** Tailscale provides an encrypted mesh network between your
devices. `API_KEY` protects remote connections; `SOLO_BYPASS=true`
(default) keeps `localhost` requests on the server itself unauthenticated.

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
    command: ["uv", "run", "assistant-sdk", "http"]
    environment:
      - API_KEY=${ALICE_KEY}
      - DEPLOYMENT_DATA_ROOT=/app/data        # user data → volume
      - DEPLOYMENT_DATA_PATH=/app/data      # project data → volume
      - API_PORT=8080
    volumes:
      - alice_data:/app/data

  bob:
    build: { context: .., dockerfile: docker/Dockerfile }
    command: ["uv", "run", "assistant-sdk", "http"]
    environment:
      - API_KEY=${BOB_KEY}
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
| Project data (cache, logs, jobs.db, templates, traces) | `data/` (`DEPLOYMENT_DATA_PATH`) | Optional (regenerable) |
| Per-user DBs | `data_root/Conversation/messages.db`, `Memory/…`, `Email/emails.db`, `Contacts/contacts.db`, `Todos/todos.db`, `Subagents/work_queue.db` | **Yes** |
| Vector index | `data_root/Memory/` (ChromaDB dirs) | Yes — but see below |
| File versions | `data_root/.versions/`, `data_root/Files/` | **Yes** |

The server is the single copy of truth — **backups are not optional**.

### SQLite databases (WAL-safe online backup)

```bash
sqlite3 "$DATA_ROOT/Conversation/messages.db" ".backup '$BACKUP_DIR/messages-$(date +%F).db'"
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

Replace the DB files / `data_root` tree with the backup, then start the
server. Keep the whole `data_root` consistent — mixing DBs from different
backup points produces a valid but inconsistent assistant.

---

## Secrets

All secrets live in `.env` (see `.env.example`). Never bake them into the
image.

| Env var | Purpose |
|---|---|
| `API_KEY` | API key for non-localhost connections (multi-device / multi-tenant) |
| `SOLO_BYPASS` | `true` (default): skip auth for localhost requests |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` | LLM provider keys |
| `OLLAMA_API_KEY`, `OLLAMA_BASE_URL` | Ollama cloud/local |
| `FIRECRAWL_API_KEY`, `FIRECRAWL_BASE_URL` | Web search/scraping (self-hosted base URL needs no key) |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `LANGFUSE_ENVIRONMENT` | Trace/observability backend |
| `CONNECTKIT_VAULT_KEY` | Set to persist OAuth tokens (Gmail/Outlook) across restarts |
| `EMAIL_GWS_CLIENT_ID`, `EMAIL_GWS_CLIENT_SECRET`, `EMAIL_M365_CLIENT_ID` | OAuth desktop client credentials |

Note: `OLLAMA_BASE_URL` defaulting to `https://ollama.com` selects the
Ollama cloud provider; point it at `http://localhost:11434` (or your own
host) for a local model server.

## Observability

- **Health**: `GET /health` and `GET /health/ready` (unauthenticated).
- **Logs**: JSONL per day at `data/logs/YYYY-MM-DD.jsonl`
  (`LOGGING_LEVEL`, `LOGGING_JSON_DIR`).
- **Traces**: Langfuse if configured (see envs above).

## Production hardening checklist

- [ ] `API_KEY` set on any server reachable beyond localhost
- [ ] TLS terminated by Caddy/ingress (never plain HTTP on a public IP)
- [ ] Per-user container/volume isolation (Mode 3)
- [ ] Run containers as non-root; rootfs read-only where possible
- [ ] Resource limits per container (memory/CPU) to protect the host
- [ ] Backups configured **and restored at least once**
- [ ] Health endpoint monitored; restart on failure

## Known gaps (as of this document)

- **No per-user authentication yet.** `user_id` is supplied by the client
  (default `default_user`); `API_KEY` authenticates the *connection*, not
  the *user*. Multi-tenant deployments must therefore be container-per-user
  and/or trusted-network only. Public multi-user hosting needs an identity
  layer (per-user tokens or OIDC) before `user_id` can be trusted from auth.
- **Teams are a skeleton.** `data/teams/{team_id}/` paths exist but nothing
  populates them — no SSO, no team scoping, no admin API.
- **Container-per-user does not scale past tens of users** on one host; the
  planned shape at that scale is an auth front + per-user workers, not more
  containers.
- **Offline/sync:** server-authoritative only. No client-side offline queue
  or bidirectional file sync yet (file cache with
  `cloud_only/downloaded/pinned` statuses is partially built).
- **One process per user store** — no horizontal scaling for a single user.

## Troubleshooting

- **Container won't start** → check the command is `uv run assistant-sdk http`
  (older docs/image references said `ea`, which was never the entry point).
- **Port mismatch** → the server listens on **8080** (the canonical port).
- **Data "disappears" after container recreation** → `DEPLOYMENT_DATA_ROOT`
  must point into the mounted volume (see Mode 3).
- **Health check fails** → `curl` is not installed in the slim image; use the
  healthcheck in `docker/docker-compose.yaml` (python `urllib`).
