# Docker Deployment Guide — multi-user trusted deployment

**Source of truth for Docker deployment.** Other docs (repo-root `DEPLOYMENT.md`,
per-project guides) are instances of this one — update here first, then sync copies.

> **Model:** single container, several **trusted** users, per-user data planes.
> This is the "Multi-tenant, **trusted (vetted) users**" mode — no container-per-user,
> no per-user passwords. **Trust prerequisite: users must not be adversarial** (§5).
> Untrusted users require container-per-user (roadmap Phase 3) — not this guide.

---

## 1. Prerequisites

- Docker + Docker Compose on any host the users can reach (VPS, Mac mini, home server)
- This repository
- One **LLM provider key** (§3)

## 2. Quick start

```bash
git clone https://github.com/open-assistants-lab/assistant && cd assistant/docker
cp .env.example .env          # then edit .env (§3)
docker compose up -d          # server on :8080
curl -s http://localhost:8080/health     # → {"status":"healthy"}
```

First boot downloads a one-time ~80MB embedding model (~15–25s) before the first
message responds. Subsequent boots are instant.

## 3. Configure `.env` — credentials live here, not in code

```bash
# ── Deployment gate (one shared key for all users) ──────────────────────
API_KEY=<generate: openssl rand -hex 24>   # remote devices send this as Bearer
SOLO_BYPASS=true                           # localhost requests skip auth

# ── LLM provider — set the key for your provider ────────────────────────
OLLAMA_API_KEY=            # ollama-cloud:<model>
OPENAI_API_KEY=            # openai:<model>
ANTHROPIC_API_KEY=         # anthropic:claude-...
GOOGLE_API_KEY=            # gemini:...
DEEPSEEK_API_KEY=
GROQ_API_KEY=
TOGETHER_API_KEY=
```

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

Every user picks a **`user_id`** and uses it in every request. That one parameter
gives them an isolated data plane:

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
curl -X POST "http://localhost:8080/profile/reload?user_id=alice"
```

## 5. The trust contract — read before onboarding

Isolation is **by convention** (everything keyed by `user_id`), not by authentication.
Be explicit with users:

| Guaranteed | NOT guaranteed (yet) |
|---|---|
| Users never *accidentally* see each other's data | A user **can** send a different `user_id` and read/write another's data — no per-user password yet |
| Per-user audit trail (who did what) | Per-user cost quotas (shared key = shared LLM bill) |
| Per-user profiles/models | Per-user key revocation (rotate = rotate for all) |
| OS-level separation from other apps | Process isolation between users (one container, one process) |

**Rule of thumb:** family and vetted teammates = fine. Adversarial users = wait for
Phase 2 per-user keys (the enforcement sweep is already wired and activates
automatically when a per-user resolver is plugged in) or use one container per user.

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

- Not multi-tenant in the **security** sense — one shared key, `user_id` is
  client-declared (per-user keys land in Phase 2; the enforcement sweep is already
  wired and will activate automatically)
- Not horizontally scalable — one container per deployment, ever (single-writer
  stores); more users = this same container until Phase 3 tenancy
- Browser automation (`agent-browser`) is not in the image — disable browser tools
  per user via capabilities, or add Chromium to a custom image