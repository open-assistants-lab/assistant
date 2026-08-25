# Assistant — Non-Tech UX Evaluation

Date: 2026-06-07

## Summary

Assistant is a deeply capable AI agent system (chat, email, web, memory,
subagents, skills, MCP, app builder). From a non-technical user perspective, the
features are compelling — but the delivery layers (install, config, interface,
onboarding) still assume a developer user.

---

## Solo Desktop — Status & Recommendations

Prioritized by impact. Non-tech user downloads a DMG and expects it to "just work."

### ✅ Done

#### Backend lifecycle management

The `.app` bundle contains the embedded backend (PyInstaller at
`Contents/Resources/ea`). The macOS AppDelegate spawns it as a subprocess on
launch, kills it on quit. Key details:
- `AppDelegate.swift` — launches backend, pipes logs to NSLog, handles crashes
  with up to 3 auto-restarts, registers as a macOS Login Item on first launch
- `backend_service.dart` (new) — Dart-side health polling of `/health` every
  500ms, exposes `BackendStatus` stream (`starting → running → crashed → stopped`)
- `agent_provider.dart` — `connect()` now waits for backend to be healthy before
  opening WebSocket; auto-connects when health passes; shows "Backend starting…"
  if user types too early; handles crash with reconnect
- **Entitlements** — Removed App Sandbox (DMG distribution, not App Store),
  added `disable-library-validation` for PyInstaller compatibility

#### README rewrite

Removed dead CLI docs. Promotes Flutter app as the primary interface. GitHub
badges, data privacy section, consolidated developer section at bottom.

#### Secrets extracted from config.yaml → .env

- `database:` block (dead), `cli:` block (dead), `api.reload/workers` (dead)
  removed entirely
- `langfuse.public_key/secret_key` removed from config.yaml → use
  `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` in `.env`
- `tools.firecrawl.api_key/base_url` removed → use `FIRECRAWL_API_KEY` /
  `FIRECRAWL_BASE_URL` in `.env`
- `LangfuseConfig` in settings.py got `env_prefix="LANGFUSE_"` so env vars
  wire through pydantic_settings
- `.env.example` cleaned up — only secrets, dead vars removed,
  grouped by category

### Must-have (remaining)

#### 1. First-run wizard ✅

Detects first launch (no provider keys configured), shows a 3-step wizard:
1. **Welcome + pick provider** — lists available LLM providers from the backend, radio selection
2. **Enter API key + Test** — text field with Test button (`✓ Connection works` / `✗`), Back button
3. **Done** — confirmation screen, "Get Started" / "Start Using Assistant" button

GoRouter redirects to `/onboarding` on first launch. `onboardingCompleteProvider` (Riverpod)
tracks tri-state (`null` = loading, `false` = not complete, `true` = done). Wizard saves API
key via `settings.setApiKey()` on completion.

After the wizard completes, the user lands on `/workspace` where a **"Learn Assistant"** checklist banner appears (modeled on Claude Code VS Code's "Learn Claude Code" flow):

| Item | Action |
|------|--------|
| 💬 Start a conversation | Shows zero-setup prompts on Canvas |
| 📧 Connect Google Workspace | Demo card for Gmail/Calendar/Contacts auth + fallback general task |
| 📋 Create and manage tasks | Canvas demo with example prompts |
| 🌐 Search the web | Canvas demo with search examples |
| 📁 Work with files | Canvas demo with file operation examples |

Each "Show me" pushes an HTML demo to the **Canvas** (WebView system) and auto-switches to the Canvas tab. The banner tracks completion via `SharedPreferences`, shows progress ("3 of 5"), and is dismissible.

**Files:**
- `lib/features/onboarding/onboarding_provider.dart`
- `lib/features/onboarding/onboarding_screen.dart`
- `lib/core/router/app_router.dart` — `/onboarding` route + redirect
- `lib/features/workspace/learn_checklist_provider.dart` — checklist state + items
- `lib/features/workspace/learn_checklist_widget.dart` — checklist UI + Canvas HTML demos
- `lib/features/workspace/workspace_panel.dart` — banner integration in workspace

#### 2. Automated DMG builds (CI/CD)

GitHub Actions that run `build_macos.sh` on tag push, code-sign the DMG, upload
to GitHub Releases. Manual builds don't ship.

### Should-have (remaining)

#### 3. First message gets to know the user ✅

On first launch (no chat history), the assistant sends a welcome message:

> "Hey there! 👋 I'm your Assistant. I'd love to get to know you — what's your name, and what brings you here? ..."

Lists possible use cases (email, calendar, tasks, research, files) and asks the user about themselves and their goals. Implemented as a local assistant message in `AgentNotifier._maybeSendWelcome()`, no API call needed.

#### 4. Auto-generated local identity ✅

Generates a UUID v4 on first launch via `main.dart`, persists in SharedPreferences as `ea_user_id`, and feeds into `userIdProvider`. The user never sees "default_user".

**Files:**
- `lib/providers/agent_provider.dart` — `resolvedUserId` module-level, `_maybeSendWelcome()`
- `lib/main.dart` — UUID generation at startup

### Nice-to-have (remaining)

#### 5. Auto-update via Sparkle

Sparkle is the standard for macOS DMG apps. Checks a release feed (GitHub
Releases or your own URL), downloads and applies updates without the user
hunting for new DMGs.

#### 6. Web + Linux builds

In the pipeline.

#### 7. Data backup hint

Settings tile: "Your data is at `~/Assistant/` — consider adding it
to iCloud Drive / Dropbox backup." The data directory is already git-initialized
— this prevents heartbreak on machine failure.

#### 8. Data migration for solo → multi-tenant

Not urgent now, but when the shared host tier lands, the solo user who signs up
for hosting needs a path to move their `~/Assistant/` data to the
remote volume (memory, contacts, todos, files, AGENTS.md).

---

## Corrected (Not an Issue)

- **"Terminal-first interface"** — CLI was removed. Flutter IS the primary
  interface now. The README just hasn't caught up.
- **"Concept overload / instincts"** — Instincts are fully removed (0 matches
  in source).

---

## Already Looking Good

- `flutter_app/lib/features/chat/widgets/` has streaming bubbles, tool call
  cards, reasoning display, error bar — solid UX foundation
- OAuth for Google/Microsoft is wired via ConnectKit — no copy-pasting tokens
- `build_macos.sh` embeds the Python backend into the `.app` bundle via
  PyInstaller — well-architected for desktop
- `backend.spec` excludes torch/PIL/tensorflow — keeps bundle size reasonable
- Secrets extracted from `config.yaml` into `.env` — first-run wizard only
  needs to write one file

---

## Multi-Tenant Architecture Analysis

### Current state

The codebase has a detailed multi-tenant design in docs but it is not built:
- `docs/DEPLOYMENT.md` — Three tiers (Solo Desktop, Shared Host, Dedicated VM)
- `docs/superpowers/path-restructuring-spec.md` — Stage 1 (solo) done, Stage 2
  (multi-tenant) designed with team scopes, `ea_team_root`, read-only team volumes
- `docs/superpowers/plans/2026-05-12-auth-deployment-plan.md` — One container
  per user, Caddy subdomain routing, API key auth
- `src/storage/paths.py` — `DataPaths` already accepts `team_id` with `team_*()`
  path methods, though `team_root` currently returns `None`

### Same frontend, two backends

The Flutter app is already architected as a thin client that can point at any
backend — the foundation exists but the UI and design docs don't cover the
transition:

- `SettingsState.host` defaults to `127.0.0.1:8080` (solo local), persisted in
  `SharedPreferences`
- `AgentNotifier.updateHost()` tears down WS and reconnects to a new host
- `WsClient` already auto-detects `ws` vs `wss` from the host string

**What's missing:**
- No UI to switch between solo and remote backends (settings shows the URL
  as read-only)
- No connection profiles (save multiple server URLs with credentials)
- No design doc covering the solo↔multi-tenant transition

### Ownership split: Solo vs Multi-Tenant

| Layer | Solo desktop | Multi-tenant |
|-------|-------------|--------------|
| Infrastructure | User — their machine | **Admin** — Docker host, gateway, volumes, uptime |
| Security boundaries | User — they own the machine | **Admin** — shell allowed commands, model allowlist, cost caps, idle timeout |
| LLM provider keys | User — their own API keys | **Admin** — provisioned centrally per container. User does not BYO |
| Model choice | User — any model | **Admin** sets allowlist, user picks within it |
| Personal data | User — `~/Assistant/` | **User** — per-user volume. Admin CAN impersonate (mounts volume) |
| Team data | N/A | **Admin** — team contacts, skills, memory, files. Users read-only |
| Personal skills/subagents | User — creates their own | **User** — creates personal ones. Admin provides team-shared |
| Personal prompt (AGENTS.md) | User | **User** — per-user volume |
| API key to connect | N/A (localhost bypass) | **Admin** — sets `API_KEY` per container |
| Billing | User pays their own LLM bills | **Admin** — usage tracking, cost allocation |

### Admin needs (not yet planned)

- **Admin UI** — Flutter app or separate: user browser, data viewer,
  impersonation ("chat as Alice")
- **Gateway admin endpoints** — route impersonation requests to the right
  container
- **Admin container** — mounts all user volumes read-only for monitoring,
  writes team data directly
- **LLM provider key provisioning** — admin sets keys at container creation,
  user never sees them
- **Model allowlist** — restrict which providers/models users can select
- **Per-user cost caps & usage tracking** — budget enforcement per container
- **Team data write path** — admin CRUD for team contacts/skills/memory/files
- **Suggested edits queue** — agents flag team data changes for admin review
- **Container health dashboard** — live status, logs, restart controls
- **Audit logs** — per-user action tracking, token usage, cost allocation

### Config layering (doesn't exist yet)

Current `config.yaml` mixes admin-level and user-level settings in one flat
file. Multi-tenant needs three layers:

1. **Image-level (admin-controlled, baked into Docker image)**
   - `shell_tool.allowed_commands` — security boundary
   - `filesystem.max_file_size_mb` — disk quota
   - `mcp.idle_timeout_minutes` — resource management
   - Model provider allowlist
   - Observability (Langfuse keys, log level)
   - `deployment.mode`

2. **Per-container env vars (admin sets per user at spawn)**
   - `API_KEY` — the user's auth key
   - `AGENT_MODEL` — default model (user can change within allowlist)
   - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — admin-provisioned
   - `FIRECRAWL_API_KEY`
   - Per-user volume mount path

3. **Per-user (user controls through the Flutter app)**
   - Tool enable/disable (capabilities)
   - Skill enable/disable
   - Personal AGENTS.md prompt
   - Email sync interval
   - MCP server configs
   - Workspace files

### What happens when switching from solo to multi-tenant

No data migration path exists. Local `~/Assistant/` stays on the
machine. The remote server has its own empty per-user volume — the user sees
a blank slate.

### Deployment diagram (amended)

```
┌─────────────────────────────────────────────────┐
│                   Gateway                        │
│  Auth (JWT) · WS routing · Container lifecycle  │
│  Admin endpoints (impersonation, monitoring)     │
└──────┬──────────────────────┬───────────────────┘
       │                      │
┌──────▼──────┐       ┌──────▼──────┐
│ Alice       │       │ Bob         │
│ Container   │       │ Container   │
│             │       │             │
│ API_KEY= │       │ API_KEY= │
│   abc123    │       │   xyz789    │
│ AGENT_MODEL=│       │ AGENT_MODEL=│
│  claude-sonnet      │  gpt-4o     │
│ Provider keys│      │ Provider keys│
│ (admin-set)  │      │ (admin-set)  │
│             │       │             │
│ Volume:     │       │ Volume:     │
│ data/users/ │       │ data/users/ │
│ alice/      │       │ bob/        │
└──────┬──────┘       └──────┬──────┘
       │                      │
┌──────▼──────────────────────▼──────┐
│         Team Volume (read-only)     │
│  data/teams/engineering/           │
│  ├── contacts.db                   │
│  ├── skills/                       │
│  ├── memory/                       │
│  └── config.yaml                   │
└────────────────────────────────────┘

┌────────────────────────────────────┐
│      Admin Console                  │
│  User browser · Data viewer        │
│  Impersonation · Team data write   │
│  Usage dashboard · Container health │
└────────────────────────────────────┘
```

### Current deployment tier readiness

| Tier | Status | Key gap |
|------|--------|---------|
| Solo desktop | ✅ Working | Packaging automation, onboarding |
| Solo WAN (Tailscale) | 🟡 API key auth exists | No Tailscale-specific doc |
| Shared Host (per-user containers) | ❌ Not built | Gateway, admin UI, config layering |
| Dedicated VM | ❌ Not built | SSO, billing, local LLM |
