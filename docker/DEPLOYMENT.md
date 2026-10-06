# Docker Deployment Guide — multi-user trusted deployment

This guide includes the existing shared-key trusted-user recipe and legacy
operator scripts. Use [builder/deployer conventions](../docs/builder-deployment-guide.md)
for application packaging and [root deployment guidance](../DEPLOYMENT.md) for
current identity/isolation boundaries. These recipes are not production
attestations or a universal installer.

> **Main recipe:** one container, several trusted users, one shared deployment
> key and client-selected namespaces. It does not enable per-user keys or OIDC,
> although both integrations exist. Users must not be adversarial (§5).
> For unrelated customers start with dedicated runtimes/storage/credentials.
> A shared uid is not hostile-tenant isolation; see also the dedicated-runtime
> generator below. Container security depends on operator configuration.

For reproducible adoption, record the reviewed image digest; `latest` examples
below are acquisition conveniences, not compatibility pins. No Docker startup
was verified in the synthetic-reference round.

---

## 1. Prerequisites

- Docker + Docker Compose on any host the users can reach (VPS, Mac mini, home server)
- This repository
- One **LLM provider key** (§3)

## 2. Quick start

### Option A — pull the published image (recommended; no clone, no build)

The image is published to GHCR on every release tag (`v*`) and every push to
`main` — multi-arch (amd64 + arm64):

```bash
docker pull ghcr.io/open-assistants-lab/assistant:latest

mkdir -p assistant-deploy && cd assistant-deploy
curl -sO https://raw.githubusercontent.com/open-assistants-lab/assistant/main/docker/docker-compose.yaml
curl -sO https://raw.githubusercontent.com/open-assistants-lab/assistant/main/docker/.env.example
curl -sO https://raw.githubusercontent.com/open-assistants-lab/assistant/main/config.yaml
mv docker-compose.yaml docker-compose.yaml.orig
# swap build: for the published image (see note below), then cp .env.example .env
docker compose up -d
curl -s http://localhost:8080/health     # → {"status":"healthy"}
```

Pull-path compose variant — replace the `build:` block in `docker-compose.yaml`
with the published image:

```yaml
services:
  app:
    image: ghcr.io/open-assistants-lab/assistant:latest
    # everything else identical to docker-compose.yaml (volumes, ports, env_file)
```

The published image ships the full feature set (`EXTRAS="--extra memory-vector
--extra analytics"` — the Dockerfile default), so semantic memory, analytics,
and the tool index all work.

### Option B — build from source

```bash
git clone https://github.com/open-assistants-lab/assistant && cd assistant/docker
cp .env.example .env          # then edit .env (§3)
docker compose up -d --build  # server on :8080 (build ~5-10 min; needs ~15GB disk)
curl -s http://localhost:8080/health     # → {"status":"healthy"}
```

> **Disk note (verified on a 25GB Linode):** building on small VPS disks is tight —
> the full image is ~12.5GB and the build cache doubles peak usage. Option A
> (pull) avoids the build entirely; run `docker builder prune -af` if the disk
> fills.

First boot downloads a one-time ~80MB embedding model (~15–25s) before the first
message responds. Subsequent boots are instant.

## 3. Configure `.env` — credentials live here, not in code

The compose file loads the **entire `.env` via `env_file`** — every key you set
there reaches the container; anything you leave unset (or commented out) falls
back to code defaults. Do NOT add `KEY=` with an empty value: empty strings
override pydantic defaults and crash bool/int settings at boot (fixed pattern:
compose uses `env_file`, not `${VAR:-}` interpolation).

```bash
# ── Deployment gate (one shared key for all users) ──────────────────────
API_KEY=<generate: openssl rand -hex 24>   # REQUIRED on public hosts (VPS) —
                                           # anyone without it gets 401
SOLO_BYPASS=false                          # public host: no localhost bypass

# ── LLM provider — UNCOMMENT the key you use ────────────────────────────
# (the .env.example ships these COMMENTED — an uncommented-but-empty key
#  yields a graceful 401 from the provider; a missing line yields no key)
OLLAMA_API_KEY=<your key>                  # e.g. ollama-cloud:deepseek-v4-flash:0731
# OPENAI_API_KEY=                          # openai:<model>
# ANTHROPIC_API_KEY=                       # anthropic:claude-...
# GOOGLE_API_KEY=                          # gemini:...
```

**VPS note (verified on a public Linode):** always set `API_KEY` + `SOLO_BYPASS=false`
on anything internet-facing — an open 8080 lets anyone burn your LLM credits. The
`config.yaml` mount is `../config.yaml` (repo root, one level up from `docker/`).

**Model selection** (precedence: per-request > per-user settings/PROFILE.md > deployment default):
1. **Per-request** — `"model": "openai:gpt-5.2"` + optional `"provider_keys": {...}` in the API call (no restart)
2. **Per-user** — that user's `PROFILE.md` (`model:`) or the model picker in settings
3. **Deployment default** — `config.yaml` → `agent.model:`

**No provider is baked in.** A deployment with no model configured fails fast with
instructions rather than silently calling a provider nobody chose.

**Admin knobs** (grader/verification, summarisation, title model) — see §6; all
settable via `.env` or `config.yaml` (mounted, edit + `docker compose restart app`,
no rebuild).

## 4. User onboarding — each user gets a namespace

In this **shared-key trusted recipe**, each client selects a `user_id` for its
namespace. That is not an authenticated account or a security boundary. With
scoped identity enabled, server-resolved identity takes precedence and mismatched
request IDs must be refused. The current shared-key recipe organises content as:

| Per-`user_id` | Where |
|---|---|
| Conversations + sessions | `data/Users/{user_id}/` message store |
| Files, memory, email, todos | per-user stores under their data dir |
| **PROFILE.md** (persona, model, skills) | `data/Users/{user_id}/PROFILE.md` |
| Model picker settings | per-user saved settings |
| Audit trail | per-user audit DB |
| Connector tokens (e.g. Gmail) | per-user vault |

Example request:

```bash
curl -X POST http://localhost:8080/v1/message \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <API_KEY>' \
  -d '{"message": "draft the weekly update", "user_id": "alice", "session_id": "weekly"}'
```

Per-user `PROFILE.md` example (`data/Users/alice/PROFILE.md`):

```markdown
---
name: alice-assistant
model: anthropic:claude-sonnet-4
skills: [writing, research]
---
You are Alice's operations assistant. Be concise; flag anything financial for review.
```

Apply profile changes without downtime:

```bash
curl -X POST "http://localhost:8080/profile/reload?user_id=alice" \
  -H 'Authorization: Bearer <API_KEY>'
```

## 5. The trust contract — read before onboarding

This recipe uses **client-selected namespaces**, not individual authentication.
Be explicit about its limits:

| Present in this recipe | Not established by this recipe |
|---|---|
| Content organised by requested `user_id` | Individual ownership enforcement: shared-key holders can select another namespace |
| Audit records keyed by namespace | Proof that the namespace identifies an authenticated person |
| Per-user profiles/models | Individual key revocation: rotating this shared key affects all holders |
| A configured container boundary | Process/OS-user isolation between users inside its shared server |

Per-user keys and browser OIDC are implemented options, not future phases.
Configure and test scoped identity if users require individual access. Do not
hand out the privileged shared deployment key as though it were a user account.
Identity enforcement does not supply separate workers or contain arbitrary code
with service credentials. Use separately configured runtime boundaries for
unrelated customers; review mounts, privileges, credentials and egress.

**Trusted-intermediary pattern (recommended for bot front ends):** run a bot/service
that authenticates users itself (whitelist, SSO, Telegram IDs) and **maps each
authenticated user to a fixed `user_id`** server-side. End users never hold the API
key or choose their own `user_id` — the intermediary is the only API client, which
contains the shared-secret limitation.

## 6. Verification & the grader (optional)

Responses can be auto-graded against a **rubric** before delivery (the
"verification" layer). Ownership: **the admin owns the grader** — model, default
rubric, tools, iterations are deployment policy (`VERIFICATION_*` in `.env` /
config.yaml `verification:`), off by default. The rubric is deliberately NOT
user-editable: the grader checks the worker's output — same party controlling both
would be self-grading. The grading prompt is hash-pinned per user, so results are
auditable. Users can toggle verification for their own account via settings; kits
may ship admin-curated rubrics.

```bash
# .env
VERIFICATION_ENABLED=true
VERIFICATION_GRADER_MODEL=            # empty = same model as the agent
VERIFICATION_DEFAULT_RUBRIC="Accurate, concise, flags financial items for review"
```

## 7. Data, backup, upgrade

- **All state** lives in `docker/data/` (bind mount) — back up that directory
  (`docker compose down` first for a consistent SQLite snapshot, or `sqlite3 .backup`
  per DB for hot copies)
- **Upgrade**: `git pull && docker compose build && docker compose up -d` — data
  survives (migrations run automatically on boot)
- **Full wipe of one user**: delete `data/Users/{user_id}/` (their data plane only);
  the default user's data lives at the mount root
- **First boot**: one-time ~80MB embedding model download before the first message

## 8. Operations & troubleshooting

| Symptom | Check |
|---|---|
| `/health` not responding | `docker compose logs app` — boot takes ~15s once (embedding model download) |
| Message returns 401 from ollama.com/openai | Provider key missing/wrong in `.env` — the stack is fine |
| Log shows `user_id missing — defaulted to 'default_user'` | A client forgot to pass `user_id` — that request landed in the shared namespace (rate-limited warning, once/hour/host) |
| User says "my data is gone" | They changed their `user_id` — data is keyed by it; ask which id they've been using |
| Audit: who did what | `curl -H 'Authorization: Bearer …' "localhost:8080/v1/audit?user_id=alice"` |

Key rotation: change `API_KEY` in `.env` → `docker compose up -d` (all users update
their Bearer token; data is unaffected).

## 9. What this mode is not

- Not hostile-tenant isolation — this recipe uses a shared key and client-selected
  namespaces. Opt-in scoped identity is available but does not create OS isolation.
- Not horizontally scalable for one user's store — never run multiple runtime
  replicas against the same mutable store. Separate user/customer runtimes are
  possible; they need independent data and credential boundaries.
- Browser automation (`agent-browser`) is not in the image — disable browser tools
  per user via capabilities, or add Chromium to a custom image
## Ollama daemon on the host (`ollama:` provider)

Models configured as `ollama:<model>` use the local OpenAI-compatible `/v1`
API and read `OLLAMA_LOCAL_BASE_URL` (not `OLLAMA_BASE_URL`). Containers cannot
reach a host daemon through `localhost`, because it names the container. On
Docker Desktop, if the daemon runs on the host, set in `.env`:

    OLLAMA_LOCAL_BASE_URL=http://host.docker.internal:11434/v1

`host.docker.internal` is Docker Desktop-specific. On Linux, `extra_hosts:
["host.docker.internal:host-gateway"]` only maps the hostname; it cannot reach
an Ollama daemon bound only to `127.0.0.1`. Either bind Ollama to a
bridge-reachable host address and restrict port 11434 to the Docker bridge with
a host firewall, or run a local reverse proxy that accepts only bridge traffic
and forwards to Ollama's loopback listener. Do not expose Ollama publicly just
to make it reachable from the container.

`OLLAMA_BASE_URL` instead configures `ollama-cloud:<model>` and defaults to
`https://ollama.com`; it does not select or configure the local `ollama:`
provider.

---

## 9. Enterprise tier — container-per-tenant-user (T3.5)

**When:** partner with isolated sub-tenants, or any deployment where users are
not vetted against each other. One container per TENANT USER; the container
boundary is the isolation tier.

- Generate: `scripts/generate_enterprise_compose.sh <user_id>...` →
  `docker/docker-compose.enterprise.yaml` (deterministic container names +
  host ports from a sha256 shard of the user_id; never two services for the
  same user_id — per-user SQLite/Chroma are single-writer)
- Per-user host dirs: `docker/enterprise/users/<user>/{data,.env}`
- Auth: per-container `API_KEY` (auth model A) or `PER_USER_AUTH=true` +
  `/auth/keys` minted keys (auth model B, see §7)
- Inside the container the Soft+UID sandbox drops sandboxed subprocesses to
  per-user UIDs (default base 2000); the container boundary adds the
  outer kernel tier
- Smoke: `bash scripts/enterprise_smoke.sh` (boots 2 users, health-checks
  both, verifies on-disk isolation, tears down; requires `docker login ghcr.io`
  or `ENTERPRISE_IMAGE=<local tag>`)
- Upgrade: per-user `docker compose pull && up -d` per container (they are
  independent services; never scale one user's service beyond 1 replica)
- Backup: per-user data dirs are self-contained — back up
  `docker/enterprise/users/<user>/data/` per user

## 10. Partner deployment packaging (T3.7)

**Partners supply application content, not customer-specific engine forks.**
Content may include profiles, custom tools, skills, domain definitions, process
safeguards and service configuration. The legacy helper below handles a narrower
profile-oriented recipe; it is not the full builder contract or a general
installer. Source installation with `uv` is also documented in the root README.

Do not run this helper unchanged against a customer. Review its paths, secret
handling, ports and version pinning against the current conventions first. Use
the [synthetic Jen reference](../examples/jen_reference/README.md) for a tested
local packaging example, not proof of live customer compatibility.

### Onboard a partner (scripted)

```bash
scripts/partner_deploy.sh -t <tenant_name> -p /path/to/PROFILE.md \
  -i ghcr.io/open-assistants-lab/assistant:vX.Y.Z -k <api_key>
```

The legacy script renders Compose, mounts a profile, starts services and invokes
profile reload. Its `/app/profile/PROFILE.md` mount is not the engine's generic
`DataPaths` profile location; verify installation against the selected release.
Health alone does not prove the intended profile/tools/domain content was loaded.

### Versioned images

`ghcr.io/open-assistants-lab/assistant:vX.Y.Z` (published on every `v*` tag;
multi-arch). Pin the tag in the generated compose file for reproducible
partner deployments.

### Bring-your-own-auth (IdentityResolver seam)

Partners choose the auth tier at deployment time; all ride the same
`IdentityResolver` seam (`src/http/auth/`):

| Option | Env | Identity resolution |
|--------|-----|--------------------|
| Shared secret | `API_KEY` | one deployment key; `user_id` accepted as-is (solo/trusted) |
| Per-user keys | `PER_USER_AUTH=true` | Bearer per-user keys → `(user_id, scopes)` via `/auth/keys` |
| Browser OIDC | `OIDC_*` settings (`/auth/oidc/login`) | Server-side identity/session resolution; not finished native SSO or a complete organisation-sharing contract |

### Adoption gate

Follow the [deployment-team checklist](../docs/builder-deployment-guide.md#adoption-checklist-for-deployment-teams).
Pin the package/runtime, configure explicit paths and identity, validate actual
loaded content, exercise safe fixture workflows and prove separate-location
recovery. A health check plus one chat round-trip is not the full acceptance gate.

## Tracing and data flows

Deployers choose observability destinations and obtain the required acceptance
for their payloads; there is no requirement to send customer traces to the
project's infrastructure. Runtime location, inference, tools and tracing are
separate data flows. Do not promise that a local runtime keeps all data local.

- **Semantic tracing:** Langfuse can receive message content, generations and
  tool spans. Review effective configuration, credentials and the explicit host.
  `LANGFUSE_ENABLED=false` overrides YAML enablement. An enabled, credentialed
  Langfuse configuration without an explicit host refuses startup.
- **Operational tracing:** configure `OTEL_ENDPOINT` and optional `OTEL_HEADERS`
  for the chosen OTLP collector. Operational spans use allowlisted runtime
  attributes, not prompt/completion/tool-result payloads. This is separate from
  Langfuse semantic content.

See [root observability guidance](../DEPLOYMENT.md#observability-ob-0ob-1).
Keep exporter credentials out of packages, logs and version control. Review the
selected release's configuration rather than copying a historical sink URL or
assuming a tracing switch alone constitutes the customer's consent.
