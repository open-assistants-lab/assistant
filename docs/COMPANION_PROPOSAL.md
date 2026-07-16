# EA Companion — Always-On AI Assistant

> A user-global, background-running personality that checks in, nudges on important tasks, and works across all workspaces. EA transforms from a reactive tool into a proactive day-to-day companion.

**Status:** Implemented (Phase 24)  
**Author:** Eddy Xu  
**Date:** May 1, 2026 (original), May 1, 2026 (major revision), May 1, 2026 (implemented)

---

## 1. The Problem

Currently, EA is **workspace-siloed and reactive-only**:

- Agent wakes up only when the user sends a message in a specific workspace
- Each workspace is fully isolated — no cross-workspace awareness
- After responding, the AgentLoop spins down completely
- No background polling, no proactive check-ins, no "care" dimension

An executive who uses EA across multiple projects (workspaces) has to manually switch, ask, and check on everything. EA is a tool, not a partner.

**What a day-to-day companion should do:**

| Capability | Current | Target |
|-----------|:---:|:---:|
| Always-on presence | No | Background loop running every N minutes |
| Cross-workspace awareness | No — siloed | User-global memory + workspace-summary awareness |
| Proactive check-ins | No — pull only | Timer/scheduler-triggered agent runs |
| Email triage | Only when asked | Background polling with nudges |
| Task/project reminders | None | Deadline-aware nudges |
| Personal context | Workspace-scoped | Global "personality" memory |
| Notification delivery | No push | In-app feed + transient toasts |
| Warm check-in | Never happens | "How's your morning going? You have 3 unread urgent emails." |

---

## 2. Competitive Landscape & Market Research

### 2.1 Deep-Dive: What Competitors Are Building

#### OpenClaw (github.com/openclaw — 367k stars, 75k forks)

The current market leader in personal AI assistants. Built around a **Gateway** (control plane) + agents + channels model.

**Architecture highlights:**

```
Gateway (control plane)
  ├── Multi-channel inbox (WhatsApp, Telegram, Slack, Discord, iMessage, Signal...)
  ├── Multi-agent routing (route channels → isolated agents)
  ├── Agent workspace (~/.openclaw/workspace)
  ├── Skills registry (ClawHub — community skill marketplace)
  ├── Heartbeat system (proactive check-ins, "heartbeats")
  ├── Cron jobs (scheduled tasks, webhooks, Gmail pub/sub)
  ├── Persistent memory (SOUL.md, agent context persistence 24/7)
  └── Companion apps
      ├── macOS menu bar app (Gateway control, voice wake, push-to-talk)
      ├── iOS node (Canvas, voice trigger forwarding)
      └── Android node (Chat, camera, screen capture, device commands)
```

**What OpenClaw gets right:**
- **Heartbeats / check-ins**: Users report being surprised by proactive reach-outs. "Apparently @openclaw checks in during heartbeats!? A kinda awesome surprise!"
- **Multi-surface delivery**: Same assistant reachable via WhatsApp, Telegram, Discord, macOS menu bar, iOS, Android — channels are just delivery pipes
- **Skills ecosystem**: ClawHub marketplace for installing capabilities. Self-hackable — the agent can build its own skills
- **Memory that moves across agents**: Context persists across Codex, Cursor, Manus, etc.
- **Autonomous loops**: "fix tests" from Telegram → runs Claude Code loop on machine → sends progress every 5 iterations
- **Background intelligence**: cron jobs, reminders, webhook-driven tasks, Gmail pub/sub

**What OpenClaw lacks:**
- **No project/workspace scoping.** The workspace is a single flat directory (`~/.openclaw/workspace`). No isolation between projects. Memory, files, conversation all mix in one bucket.
- **No desktop app UI.** It's gateway + CLI + menu bar. No rich desktop panel with timeline feed, no visual workspace browser. Canvas exists but is agent-driven visual, not structured project management.
- **Heartbeats are basic.** A periodic ping, not context-aware cross-project intelligence. No pre-computed workspace summaries, no email urgency assessment.
- **Channel-first, not app-first.** Designed for "text me on WhatsApp," not for "I'm sitting at my desk working in this project."

#### Claude Code Desktop (code.claude.com)

Anthropic's desktop app with three tabs: Chat, Cowork, Code.

**Architecture highlights:**

```
Desktop App
  ├── Chat tab — general conversation, no file access
  ├── Cowork tab — autonomous background agent in cloud VM
  │   └── Runs independently while you do other work
  ├── Code tab — interactive coding with local file access
  │   ├── Sessions (parallel, each in git worktree)
  │   ├── Routines (scheduled autonomous runs on cloud)
  │   │   ├── Schedule trigger (hourly/daily/weekly/cron)
  │   │   ├── API trigger (POST → fire routine)
  │   │   └── GitHub trigger (PR opened/merged/released)
  │   └── Desktop scheduled tasks (run on local machine)
  ├── Plugin system (install skills, agents, MCP servers)
  ├── Drag-and-drop layout (chat, diff, terminal, file, preview panes)
  └── Cross-surface continuity (web, iOS, desktop, VS Code, JetBrains)
```

**What Claude Code Desktop gets right:**
- **Routines are the companion primitive.** Schedule-driven, trigger-driven, fully autonomous. A "PR review at 9am daily" is exactly the companion pattern but scoped to code.
- **Cowork is a background agent.** Runs in a cloud VM, independent of what you're doing locally. The closest thing to an "always-on" agent in a major product.
- **Cross-surface continuity.** Start on desktop, continue on phone, pick up in terminal. Sessions aren't tied to one surface.
- **Permission modes.** Ask permissions, auto accept, plan mode — graduated trust.
- **Parallel sessions.** Multiple agent conversations on the same codebase, each in its own git worktree.

**What Claude Code Desktop lacks:**
- **No proactive nudge across non-code domains.** Routines and Cowork are code-only. No email triage, no deadline tracking across projects, no personal check-in.
- **Routines are per-repo, not cross-repo.** A routine runs against one repository. No cross-project awareness.
- **No personal rapport.** Claude Code is a coding tool, not a companion. No personality memory, no warmth, no check-ins.

#### OpenCode (opencode.ai — 150k stars)

Open-source coding agent with LSP integration, multi-session, desktop app beta. Primarily a terminal/IDE coding agent. No companion features, no workspace concept beyond project directories. Relevant as evidence of the "desktop agent" form factor becoming standard.

#### Perplexity Spaces

Research workspaces with custom AI instructions, uploaded files, persistent context. 5M+ Spaces created. Not proactive — purely reactive research. But **Spaces validate the multi-project container model**: users clearly want named, isolated project environments with custom instructions.

#### Codex Desktop (OpenAI)

Project directories with `.codex/` config, `AGENTS.md` instructions, three-level scoping (enterprise → project → personal). Sessions are disposable, configuration is persistent. Mirrors Claude Code's model — project-scoped agents with hierarchical config fallback.

### 2.2 Market Convergence Analysis

The market is converging on four patterns, but no single product combines all of them:

| Pattern | Products Doing It |
|---------|-------------------|
| **Multi-project isolation** (workspaces/spaces/projects) | EA ✅, Perplexity, Claude Code, Codex |
| **Background autonomy** (routines/scheduled/heartbeats) | Claude Code (Routines), OpenClaw (heartbeats) |
| **Personal companion warmth** (rapport, check-in, personality) | OpenClaw (SOUL.md, heartbeats — emerging), Replika/Pi (pure companion) |
| **Rich desktop UI** (multi-panel, timeline, visual feed) | Claude Code Desktop, EA (Flutter) |

**EA already has #1 (workspaces) and #4 (Flutter desktop). The Companion adds #2 (background) + #3 (warmth).**

### 2.3 Updated Market Matrix

| Platform | Type | Proactive? | Background? | Workspace-aware? | Professional focus? | Companion warmth? | App-first UI? |
|----------|------|:---:|:---:|:---:|:---:|:---:|:---:|
| **OpenClaw** | Personal AI | ⚠️ Heartbeats (basic) | Yes (gateway daemon) | No — single flat workspace | Yes — full scope | ⚠️ SOUL.md persona, but task-focused | No — channel-first (WhatsApp/Telegram) |
| **Claude Code Desktop** | Coding agent | ⚠️ Routines (schedule + trigger) | Yes (Cowork in cloud VM) | No — per-repo sessions | Yes — code only | No | Yes — rich desktop app |
| **OpenCode** | Coding agent | No | No | No — single project | Yes — code only | No | Desktop beta |
| **Perplexity Spaces** | Research workspace | No | No | ✅ Per-space instructions + files | Yes — research only | No | Web only |
| **Codex Desktop** | Coding agent | No | No | ⚠️ Project=repo, not multi-domain | Yes — code only | No | Yes — desktop app |
| **Motion / Clockwise** | AI calendar | Yes (auto-schedule) | Yes | No | Yes — calendar only | No | Yes — desktop + web |
| **Superhuman** | AI email | Yes (reminders, triage) | Yes | No | Yes — email only | No | Yes — desktop + mobile |
| **Replika / Pi** | AI companion | Yes (check-ins) | Yes | No | No — emotional/friend | ✅ High | Mobile-first |
| **EA (current)** | Multi-project agent | No | No | ✅ Full workspace isolation | Yes — multi-domain | No | Yes — Flutter desktop |
| **EA Companion (proposed)** | Always-on executive AI | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** |

### 2.4 Key Competitive Insights

**OpenClaw's heartbeats are the closest comp.** Users love them — tweets like "Apparently @openclaw checks in during heartbeats!?" show delight. But heartbeats lack context awareness. They're a ping, not an intelligent nudge. EA can do better by pre-computing cross-workspace context.

**Claude Code's Routines are the scheduling gold standard.** Three trigger types (schedule, API, GitHub) + cloud VM execution + cross-surface continuity. This is exactly the infrastructure pattern the companion scheduler should eventually grow into. For V1, a simple timer is fine — the companion doesn't need to persist beyond the app session.

**No product combines warmth with professional scope.** OpenClaw is task-focused (fix this, check that). Replika is emotion-focused (how are you feeling?). EA Companion sits in between: warm but professional, aware of your projects but checking in on you as a person.

**"App-first" is a differentiator.** OpenClaw is channel-first (reach me on WhatsApp). EA is app-first (I'm sitting at my desk, working in this project). The companion experience in a rich desktop app can be richer than a WhatsApp message — it can show structured context, workspace cards, actionable buttons.

---

## 3. Workspace Alignment

### 3.1 Current Workspace Architecture (as implemented April 30, 2026)

The workspace system is fully built and tested (521 tests passing):

```
DataPaths per workspace:
  workspace_files_dir()       → ~/EA/Workspaces/{ws_id}/files/
  workspace_memory_dir()      → ~/EA/Workspaces/{ws_id}/memory/
  workspace_subagents_dir()   → data/workspaces/{ws_id}/subagents/
  workspace_conversation_dir()→ data/workspaces/{ws_id}/conversation.app.db
  workspace_skills_dir()      → Workspaces/{ws_id}/skills/

Workspace model:
  Workspace(id, name, description, custom_instructions, created_at, updated_at)

Available APIs:
  list_workspaces()           → list[Workspace]
  load_workspace(ws_id)       → Workspace | None
  save_workspace(ws)          → persist YAML

Per-workspace AgentLoop cache:
  runner.py: _loop_cache keyed by "user_id:workspace_id"
```

### 3.2 How the Companion Uses Workspaces

The companion is **user-global** — it sits above workspaces. It reads workspace metadata and activity summaries to build context. It never writes to workspace memory or files.

```
┌──────────────────────────────────────────────────────────────┐
│                     User Process                               │
│                                                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │             Companion Scheduler (user-global)            │  │
│  │  • Own AgentLoop — NOT in _loop_cache                    │  │
│  │  • Context builder aggregates ALL workspaces             │  │
│  │    - Calls list_workspaces() to enumerate                │  │
│  │    - Summarizes recent activity per workspace            │  │
│  │    - Counts urgent emails (user-level, not per-ws)       │  │
│  │    - Reads companion_memory for personality facts        │  │
│  │  • Output → companion_notifications (with ws_id)         │  │
│  │  • Notification delivery → Flutter (4 surfaces)          │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                    │
│  │ Workspace│  │ Workspace│  │ Workspace│    ...             │
│  │ Personal │  │ Q2 Plan  │  │ Home Reno│                    │
│  │  ● AgentLoop           │  │          │                    │
│  │  ● MemoryMiddleware    │  │          │                    │
│  │  ● own cache key       │  │          │                    │
│  └──────────┘  └──────────┘  └──────────┘                    │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 Context Builder — Workspace-Aware (Updated from Original)

```python
def _build_context(self) -> str:
    from src.sdk.workspace_models import list_workspaces

    # Aggregate across ALL workspaces
    workspaces = list_workspaces()
    ws_summaries = []
    for ws in workspaces:
        activity = summarize_workspace_activity(ws.id)  # reads that ws's conversation/memory
        if activity:
            ws_summaries.append(f"  - {ws.name}: {activity}")

    urgent = count_urgent_emails_across_all_accounts(self.user_id)
    hour = datetime.now().hour
    tod = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    last = self._db.last_check_time()

    # Pull companion personality facts
    personality = self._memory_db.get_all()

    return f"""## Check-in context

TIME: {datetime.now().strftime('%I:%M %p %A %B %d')} ({tod})
URGENT UNREAD EMAILS: {urgent}
WORKSPACES ({len(workspaces)} total):
{chr(10).join(ws_summaries) if ws_summaries else '  No recent activity across any workspace.'}

WHAT I KNOW ABOUT THE USER:
{chr(10).join(f'  - {k}: {v}' for k, v in personality.items()) or '  nothing yet'}

LAST CHECK-IN: {last or 'first check-in ever'}
PREVIOUS MESSAGES (avoid repeating): {self._db.recent_messages(3)}

Decide: nudge the user or skip?"""
```

**Key difference from original:** `summarize_workspace_activity()` now iterates `list_workspaces()` rather than a flat user-level summary. The companion sees what's happening in each project.

**Important:** Activity reports should include the "personal" workspace. The runner suppresses workspace context for "personal" (`runner.py:76` returns `""`), but the companion context builder is NOT the workspace system prompt — it should report all workspace activity equally.

---

## 4. Proposed Architecture

### 4.1 Core Concept: Companion Scheduler (Not in AgentLoop Cache)

Assistant's workspace AgentLoops live in `runner.py:_loop_cache`, keyed by `user_id:workspace_id`. The companion AgentLoop must NOT enter this cache because:

1. The cache key is `user_id:workspace_id` — no workspace concept
2. It would share lifecycle with workspace loops (reset, expiry)
3. It needs independent configuration (different provider, no tools, different system prompt)

```python
class CompanionScheduler:
    """Per-user background scheduler. Owned by the HTTP app lifecycle."""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self._task: asyncio.Task | None = None
        self._paused = False
        self._db = CompanionNotificationDB(user_id)
        self._memory_db = CompanionMemoryDB(user_id)
        self._loop: AgentLoop | None = None

    async def start(self):
        self._loop = self._create_loop()
        self._task = asyncio.create_task(self._run())

    async def pause(self):
        self._paused = True

    async def resume(self):
        self._paused = False

    def _create_loop(self) -> AgentLoop:
        provider = create_model_from_config("ollama:minimax-m2.5")
        return AgentLoop(
            provider=provider,
            tools=[],  # NO tool calls in V1
            system_prompt=COMPANION_SYSTEM_PROMPT,
            middlewares=[],  # No memory middleware — companion has its own store
            run_config=RunConfig(max_llm_calls=2, cost_limit_usd=0.01),
            user_id=self.user_id,
        )

    async def _run(self):
        while True:
            if not self._paused:
                try:
                    await self._cycle()
                except Exception as e:
                    logger.error("companion.cycle_failed", 
                                 {"error": str(e)}, user_id=self.user_id)
            await asyncio.sleep(self._next_interval() * 60)

    async def _cycle(self):
        ctx = self._build_context()
        result = await self._loop.run([Message.user(ctx)])
        text = self._extract_response(result)
        if text and text.strip().upper() != "[SKIP]" and len(text.strip()) > 3:
            category, ws_id = self._categorize(text)
            self._db.insert(text, category, ws_id)
```

### 4.2 Companion Memory (User-Global, Dedicated Store)

Located at `data/users/{user_id}/companion/memory.db` — follows existing DataPaths pattern.

```sql
CREATE TABLE companion_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'inferred',
    confidence REAL DEFAULT 0.5,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, key)
);

CREATE INDEX idx_comp_mem_user ON companion_memory(user_id);
```

Stores personality facts the companion learns over time:
- `mood_checkin_style` → `brief_direct` (0.8 — learned from 3 dismisses)
- `preferred_hours` → `8-18` (0.9 — learned from consistent activity)
- `important_contacts` → `["sarah@acme.com", "tom@benton.ai"]` (0.7)
- `morning_routine` → `email first, then tasks` (0.6)

Confidence decays at 0.01/day. Sources: `inferred` (from dismiss/engage patterns), `explicit` (user told companion directly), `learned` (from conversation patterns).

### 4.3 Notification Store (Updated with workspace_id)

```sql
CREATE TABLE companion_notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,
    category TEXT DEFAULT 'general',  -- 'general', 'urgent', 'email', 'deadline'
    workspace_id TEXT,                -- NULL = user-global, "q2-planning" = workspace-scoped
    dismissed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_comp_notif_user ON companion_notifications(user_id, dismissed, created_at);
CREATE INDEX idx_comp_notif_ws ON companion_notifications(workspace_id, dismissed);
```

**`workspace_id` purpose:**
- `NULL`: General check-ins ("Good morning!"), visible everywhere
- `"q2-planning"`: Workspace-specific nudges. Inline chat nudge (Surface 4) only fires when active workspace matches. Companion panel shows all notifications regardless.

### 4.4 Companion System Prompt (Updated)

```
You are Assistant's companion personality — a warm, attentive assistant
that checks in throughout the day. You work across ALL the user's workspaces
(projects), maintaining awareness of what's happening everywhere.

Your job: read the context below (time, email urgency, workspace activity,
what you know about the user) and decide to either:

  A) Write ONE brief, warm check-in message (1-2 sentences max)
     if something deserves the user's attention, or

  B) Skip this cycle entirely (just output '[SKIP]')

Tone: warm but professional. Like a great EA, not a chatbot.
Never repeat yourself. Vary your phrasing across cycles.
Morning: energetic. Midday: focused. Evening: reflective.

If the user has urgent emails → gentle nudge.
If specific workspaces have activity → mention which one.
If multiple workspaces have urgent items → pick the most impactful.
If workspaces are quiet → a brief hello or skip.
If late at night → skip.

You are NOT a chatbot. You are an executive's personal assistant who
happens to check in periodically. Be brief. Be useful. Be warm.
```

### 4.5 HTTP Endpoints (Updated)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/companion/notifications?user_id=&limit=50` | List notifications (most recent first) |
| `POST` | `/companion/notifications/{id}/dismiss` | Mark notification as dismissed |
| `POST` | `/companion/pause` | Pause the scheduler |
| `POST` | `/companion/resume` | Resume the scheduler |
| `GET` | `/companion/status?user_id=` | Current status (running/paused/error, last check-in time) |
| `GET` | `/companion/memory?user_id=` | List all companion personality facts |
| `DELETE` | `/companion/memory/{id}` | Delete a personality fact |

**File: `src/http/routers/companion.py` (NEW, ~100 lines)**

### 4.6 HTTP App Lifecycle

```python
# src/http/main.py

_companion_schedulers: dict[str, CompanionScheduler] = {}

@app.on_event("startup")
async def startup():
    settings = get_settings()
    if getattr(settings, "companion_enabled", False):
        # Start per-user companion for default user (future: all active users)
        scheduler = CompanionScheduler("default_user")
        await scheduler.start()
        _companion_schedulers["default_user"] = scheduler
        logger.info("companion.started", {}, user_id="system")

@app.on_event("shutdown")
async def shutdown():
    for scheduler in _companion_schedulers.values():
        await scheduler.stop()
    logger.info("companion.stopped", {}, user_id="system")
```

---

## 5. UI/UX Design — Reimagined

### 5.1 Design Philosophy

The companion's UX follows **progressive disclosure** across four surfaces, from ambient to engaged:

| Surface | Urgency | Interaction | Purpose |
|---------|---------|-------------|---------|
| 1. Sidebar Pulse | Ambient | Glance only | "Is EA watching?" — zero-attention awareness |
| 2. Inline Nudge | Contextual | Read only (in-chat) | "This is relevant to what you're doing right now" |
| 3. Toast Overlay | Urgent | Action (Open/Dismiss) | "You need to know this NOW" — transient, self-dismissing |
| 4. Companion Feed | Engaged | Full browse + actions | "What has EA been noticing?" — deliberate engagement |

This mirrors how real assistants work: you notice they're present (pulse), they whisper context in meetings (inline nudge), they interrupt for urgent matters (toast), and you review their notes later (feed).

### 5.2 Updated Layout (Reflects Built Workspace Architecture)

The sidebar already has a workspace switcher. Companion sits alongside it.

```
┌──────────────┬───────────────────────────┬──────────┬──────────────────────┐
│   Sidebar    │        Chat Panel         │ Divider  │    Content Panel      │
│  (240px)     │      (variable)           │  (1px)   │    (variable)         │
│              │                           │          │                       │
│ ● EA watch   │ [q2-planning] [personal]  │          │  ● Companion          │
│  [Search]    │ ────────────────────────  │          │  ─────────────────    │
│ ─────────    │                           │          │                       │
│ Workspaces   │  You: check the status    │          │  Today                │
│ • personal ○ │                           │          │                       │
│ • project-a  │  ┌─────────────────────┐ │  │          │  ┌────────────────┐  │
│ • project-b● │  │ 💡 You have 3       │ │  │  12 min  │  │ 🔔 10:43 AM    │  │
│              │  │ overdue tasks in    │ │  │  ago     │  │ 3 overdue tasks │  │
│ ─────────    │  │ project-b. Tap to   │ │  │          │  │ in Q2 Planning  │  │
│ ● Companion  │  │ switch workspace?   │ │  │          │  │ [Switch] [×]    │  │
│ Memory       │  └─────────────────────┘ │  │          │  │                 │  │
│ Skills       │                           │  │          │  └────────────────┘  │
│ Subagents    │  EA: Pipeline build is    │  │          │                       │
│ Settings     │  green. Last deploy:      │  │          │  ┌────────────────┐  │
│              │  9:15 AM. All tests pass. │  │          │  │ 💬 9:45 AM     │  │
│              │                           │  │          │  │ Morning Eddy!  │  │
│              │  [Chat input...]          │  │          │  │ Pipeline is     │  │
│              │                           │  │          │  │ quiet. Enjoy ☕  │  │
│              │                           │  │          │  └────────────────┘  │
│              │                           │  │          │                       │
│              │                           │  │          │  ┌────────────────┐  │
│              │                           │  │          │  │ ✅ 9:00 AM     │  │
│              │                           │  │          │  │ Pipeline build  │  │
│              │                           │  │          │  │ green. All tests│  │
│              │                           │  │          │  │ pass. 42 checks.│  │
│              │                           │  │          │  └────────────────┘  │
│              │                           │  │          │                       │
│              │                           │  │          │  [Pause companion ⏸]  │
└──────────────┴───────────────────────────┴──────────┴──────────────────────┘
```

### 5.3 Surface 1: Sidebar Pulse (Redesigned)

Previous design was text-heavy ("EA active ●"). New design is purely visual — a breathing pulse animation that communicates status without words.

```
┌──────────────────┐
│ ○○○○○○○○○○○○○○ │  ← breathing pulse (warm accent color)
│                  │     ○○○○ = companion running (subtle animation)
│ [Search chats...]│     ·    = companion paused (static, dim)
│ ───────────────  │     —    = companion error/off (invisible)
│                  │     Tap toggles pause/resume
│                  │     Long-press → companion settings
```

**Widget:** `CompanionPulse` — a `CustomPaint` widget with a 3-circle animation (inner solid, middle expanding ring, outer fading ring). Duration: 4s looping. Color: `AppColors.accentWarm` when active, `AppColors.textDim` when paused.

This is inspired by OpenClaw's menu bar app which shows gateway status as a simple colored dot. Assistant's version is more expressive — it communicates "I'm here, I'm watching" through motion rather than text. No reading required. You feel the presence.

Lines: ~40 (custom painter + animation controller)

### 5.4 Surface 2: Inline Chat Nudge (Refined)

**Design update:** Instead of a distinct "companion_nudge" message type, we use a **compact pill** that appears between messages when relevant. It's not a message — it's a contextual suggestion from the companion layer.

```
┌─────────────────────┐
│ [q2-planning] tab   │
│ ──────────────────  │
│                     │
│ You: what's the     │
│ budget status?      │
│                     │
│ ┌─────────────────┐ │
│ │ 💡 You have new  │ │  ← "CompanionContextPill"
│ │ budget update    │ │     Not a message — a contextual insert.
│ │ from Sarah       │ │     Appears between messages when companion
│ │ [Open] [Ignore]  │ │     detects something relevant to current ws.
│ └─────────────────┘ │     Sits above Assistant's response, not interrupting.
│                     │     Background: warm accent at 8% opacity.
│ EA: Let me check    │     Left border: 2px accent.
│ the latest budget   │     Font: 12px, secondary text color.
│ data...             │
```

**Widget:** `CompanionContextPill` — a `Container` with `BoxDecoration(border: Border(left: BorderSide(color: accent, width: 2)))`, inserted into the chat `ListView` via `SliverList` delegate.

**When it appears:**
- Companion notification has `workspace_id` matching active workspace
- Notification was created in the last hour
- User hasn't dismissed it yet

Lines: ~25 (reuses padding/spacing patterns from `MessageBubble`)

### 5.5 Surface 3: Toast Overlay (Polished)

Refined from original — now with gesture-aware dismiss (swipe up), not just auto-timeout.

```
┌───────────────────────────────────────────────────────────────┐
│                                                               │
│         ┌─────────────────────────────────────────┐          │
│         │ ▎⚠  3 overdue tasks in Q2 Planning      │          │
│         │ ▎    Sarah's been waiting 2 days.        │          │
│         │ ▎                                       │          │
│         │ ▎    [Switch to Q2 Planning]  [Dismiss]  │          │
│         └─────────────────────────────────────────┘          │
│                                                               │
│  ┌──────────┬─────────────────────┬──────────┬────────────── │
│  │ Sidebar  │     Chat Panel      │ Divider  │ Content Panel │
```

**Design specs (updated):**
- Max width: 420px (down from 480px — more focused)
- Left border: 3px `AppColors.warning` (amber, not accent — urgency should feel different from brand color)
- Background: `AppColors.surface` with subtle amber gradient at top (2px)
- Shadow: `BoxShadow(blurRadius: 16, offset: Offset(0, 4), color: Colors.black26)`
- Icon: `Icons.warning_amber_rounded`, 18px, `AppColors.warning`
- Enter: slide down from top (easeOutBack, 400ms) — feels like it "arrives"
- Exit options:
  - Auto-dismiss after 8 seconds: slide up + fade (easeIn, 250ms)
  - Swipe up gesture: user can flick it away faster
  - Tap "Dismiss" button: instant dismiss

**When it appears:**
- Companion notification with `category == "urgent"`
- Only one toast at a time (queue urgent notifications, show oldest first)

Lines: ~55

### 5.6 Surface 4: Companion Feed (Redesigned)

The biggest redesign from the original. Instead of a simple list of notification cards, this is now a **timeline feed with workspace context cards**. Each entry shows:
- What happened
- Which workspace it relates to
- When it occurred
- Actions the user can take

```
┌────────────────────────────────────────┐
│ ● Companion                   12:43 PM │  ← Header: icon + "Companion" + status time
│ ────────────────────────────────────── │     "12:43 PM" = last check-in. Updates live.
│                                        │     Right-aligned, small, secondary text.
│                                        │
│ ┌── Today ──────────────────────────┐ │  ← Date section header (sticky)
│ │                                   │ │
│ │ ● 10:43 AM    Q2 Planning — Task  │ │  ← Entry format:
│ │               ▸ 3 overdue tasks   │ │     ● time = urgency dot (warm or dim)
│ │                 Switch to Q2      │ │     ws name = linked to workspace
│ │                 Planning?         │ │     ▸ = action prompt (verb-first)
│ │                    [Switch] [×]   │ │     description = companion's message
│ │                                   │ │     actions = contextual buttons
│ │ ● 9:45 AM     Personal — Check-in│ │
│ │               ▸ Good morning Eddy│ │     Check-ins have a "chat" bubble style,
│ │                 Pipeline is quiet │ │     no workspace name needed. Just warmth.
│ │                 today. Enjoy ☕.  │ │
│ │                        [Thanks]  │ │     [Thanks] = quick acknowledge, dims entry
│ │                                   │ │
│ │ ● 9:00 AM     Pipeline — CI/CD   │ │  ← Each notification card has:
│ │               ▸ Pipeline build   │ │     - 2px left accent border
│ │                 green. All 42     │ │     - Workspace badge (small chip)
│ │                 tests pass.       │ │     - Message body (14px, 1-2 lines)
│ │                                   │ │     - Action row (below body, right-aligned)
│ │                                   │ │
│ └───────────────────────────────────┘ │
│                                        │
│ ┌── Yesterday ──────────────────────┐ │
│ │                                   │ │
│ │ ○ 4:30 PM     Home Reno — Update  │ │  ← Dismissed/dimmed entries:
│ │               ▸ Sarah accepted    │ │     ○ = read/acknowledged
│ │                 the revised quote.│ │     Dimmer text, no left border.
│ │                        [Restore]  │ │     [Restore] = un-dismiss
│ │                                   │ │
│ └───────────────────────────────────┘ │
│                                        │
│ ═══════════════════════════════════ │  ← Footer separator
│                                        │
│ Companion running · Last check: 2m ago │  ← Status line
│ [Pause companion]                      │  ← Footer action
└────────────────────────────────────────┘
```

**Key UX decisions:**

1. **Workspace badges, not full names.** "Q2 Planning" becomes "Q2". Each workspace gets a compact badge chip (rounded pill, 10px font, workspace color). Clickable → switches to that workspace.

2. **Entry categories drive iconography:**
   - `email` → mail icon, accent left border
   - `deadline` → calendar-alert icon, warning left border
   - `checkin` → chat bubble icon, subtle left border (no urgency)
   - `ci/cd` → check-circle icon, success left border

3. **Actions are contextual, not generic:**
   - Email + workspace match → [Switch to {ws}]
   - Deadline + workspace match → [Open {ws}]
   - Check-in → [Thanks] (just acknowledge warmth)
   - CI/CD → [Show details] or nothing (informational)

4. **Acknowledge, don't just dismiss.** "Dismiss" is cold. "Thanks" for check-ins, "Got it" for status updates, "Switch" for tasks. Dismiss (×) is always available but not the primary action.

5. **Empty state (first launch) feels welcoming, not empty:**
   ```
   ┌──────────────────────────────┐
   │ ● Companion                  │
   │ ──────────────────────────── │
   │                              │
   │           🫧                 │  ← Large bubble icon (60px, warm accent)
   │                              │
   │     I'm watching across      │  ← Centered text, 14px, secondary
   │     your 3 workspaces.       │
   │     I'll let you know if     │
   │     anything needs your      │
   │     attention.               │
   │                              │
   │     Until then — carry on.   │
   │                              │
   └──────────────────────────────┘
   ```

6. **Paused state:**
   ```
   ┌──────────────────────────────┐
   │ ○ Companion paused           │
   │ ──────────────────────────── │
   │                              │
   │           ⏸                  │
   │     Companion is paused.     │
   │     No check-ins until you   │
   │     resume.                  │
   │                              │
   │     [Resume companion]       │
   └──────────────────────────────┘
   ```

**Widget structure:**
- `CompanionFeed` (StatefulWidget — polls notifications every 30s)
  - `_CompanionHeader` (icon + title + last check-in time)
  - `_CompanionTimeline` (ListView.builder, date-grouped sections with sticky headers)
  - `_CompanionEntry` (card: workspace badge + icon + body + actions)
    - Three variants based on `category`: `CompanionEmailEntry`, `CompanionDeadlineEntry`, `CompanionCheckinEntry`
  - `_CompanionFooter` (status line + pause button)
  - `_CompanionEmptyState` (shown when no notifications)

Lines: ~180 (panel + header + timeline + 3 entry variants + footer + 2 empty states)

### 5.7 State Management (Updated)

| Provider | Type | Purpose |
|----------|------|---------|
| `companionPausedProvider` | `StateProvider<bool>` | Is companion paused? (default: false) |
| `companionStatusProvider` | `FutureProvider<CompanionStatus>` | Running/paused/error + last check-in time. Polls `/companion/status` every 30s |
| `companionNotificationsProvider` | `FutureProvider<List<CompanionNotification>>` | Notification list from API. Polls `/companion/notifications` every 30s when feed visible, 2min otherwise |
| `companionActiveToastProvider` | `StateProvider<CompanionNotification?>` | Active toast (null = hidden) |
| `companionMemoryProvider` | `FutureProvider<List<CompanionMemoryFact>>` | User personality facts. Loaded once when feed opens |

**Toast logic:**
```dart
// In CompanionNotifier (or a Riverpod listener):
void _maybeShowToast(List<CompanionNotification> newList, List<CompanionNotification> oldList) {
  final newUrgent = newList
    .where((n) => n.category == 'urgent' && !n.dismissed)
    .toList();
  
  if (newUrgent.isNotEmpty && ref.read(companionActiveToastProvider) == null) {
    ref.read(companionActiveToastProvider.notifier).state = newUrgent.first;
    // Auto-dismiss after 8 seconds
    Future.delayed(const Duration(seconds: 8), () {
      if (mounted) ref.read(companionActiveToastProvider.notifier).state = null;
    });
  }
}
```

### 5.8 Flutter Integration Points (Updated)

```
DesktopLayout.render
├── Stack (root)
│   ├── Row (main layout)
│   │   ├── _Sidebar
│   │   │   ├── + CompanionPulse          ← NEW (Surface 1), above search
│   │   │   ├── WorkspaceSwitcher (existing)
│   │   │   ├── WorkspaceList (existing)
│   │   │   └── BottomNav
│   │   │       └── + DesktopSidebarItem.companion  ← NEW enum, FIRST position
│   │   ├── Divider
│   │   ├── _ChatPanel
│   │   │   └── ChatListView
│   │   │       └── + CompanionContextPill  ← NEW (Surface 2), insert between messages
│   │   ├── Divider
│   │   └── ContentPanel (routed)
│   │       └── + CompanionFeed (route '/companion')  ← NEW (Surface 3)
│   │
│   └── Positioned (top-center)
│       └── + CompanionToast (Surface 4)  ← Overlay, visible when activeToast != null
```

**Lines of Flutter code: total ~300 (up from ~220 in original, due to richer feed design)**

---

## 6. Implementation Plan (Updated)

### 6.1 Phase 1: Backend (~180 lines, 1-2 days)

| # | What | Lines | New File? |
|---|------|-------|-----------|
| 1 | `CompanionNotificationDB` SQLite + helpers | ~30 | NEW `src/sdk/tools_core/companion_db.py` |
| 2 | `CompanionMemoryDB` SQLite + confidence decay | ~40 | Same file |
| 3 | `CompanionScheduler` class (start/pause/resume/stop, adaptive interval, workspace-aware `_build_context()`) | ~80 | NEW `src/sdk/companion_scheduler.py` |
| 4 | Companion system prompt (refined) | ~15 | In scheduler file |
| 5 | Wire scheduler into HTTP app lifecycle | ~15 | MODIFY `src/http/main.py` |
| 6 | `GET/POST /companion/notifications` + dismiss/pause/resume/status/memory endpoints | ~60 | NEW `src/http/routers/companion.py` |

### 6.2 Phase 2: Flutter (~300 lines, 2-3 days)

| # | What | Lines | New File? |
|---|------|-------|-----------|
| 7 | `CompanionPulse` animated status indicator (CustomPaint + AnimationController) | ~40 | NEW `lib/features/companion/companion_pulse.dart` |
| 8 | `CompanionContextPill` inline nudge widget | ~25 | NEW `lib/features/companion/companion_context_pill.dart` |
| 9 | `CompanionToast` overlay (slide enter/exit, gesture dismiss) | ~55 | NEW `lib/features/companion/companion_toast.dart` |
| 10 | `CompanionFeed` — header + timeline + date grouping | ~80 | NEW `lib/features/companion/companion_feed.dart` |
| 11 | `CompanionEntry` — 3 variants (email, deadline, checkin) + actions | ~60 | NEW `lib/features/companion/companion_entry.dart` |
| 12 | Empty states (first launch, paused) | ~20 | Same as feed |
| 13 | Riverpod providers (4 providers) + API client methods | ~20 | MODIFY `lib/core/api_client.dart` + new provider file |
| 14 | Router + sidebar enum + DesktopLayout integration | ~0 | MODIFY existing files (trivial wiring) |

### 6.3 Phase 3: Polish & Testing (1 day)

| # | What |
|---|------|
| 15 | Unit tests: `CompanionScheduler` context builder, notification DB, memory DB (~25 tests) |
| 16 | HTTP endpoint tests: all companion endpoints (~10 tests) |
| 17 | Flutter integration: pulse animation, toast overlay timing, feed scroll performance |
| 18 | Edge case handling: companion failure fallback, rapid pause/resume toggling, empty workspace list |

**Total: ~480 lines backend + frontend. 4-6 days for a solo developer.**

---

## 7. Zero-Risk Rollout

Unchanged from original — all safety guarantees remain:

1. **Companion is opt-in.** Environment variable `COMPANION_ENABLED=true`. Default: off.
2. **No tool calls in V1.** All context is pre-computed deterministically. LLM has zero access to tools, files, or email content. It reads pre-digested context and produces text only.
3. **Companion runs in its own AgentLoop.** Separate from workspace loops. Not in `_loop_cache`. Independent lifecycle.
4. **Cost-controlled.** Local model by default (`ollama:minimax-m2.5` — free). Cloud models: `cost_limit_usd=0.01` per cycle. At 15-min intervals, max $0.04/hour.
5. **Fallback to silence.** Scheduler skips cycles on failure. User never sees errors.
6. **Single-user model.** Companion scheduler is per-user. Scales to team/enterprise later.

---

## 8. Risks & Mitigations (Updated)

| Risk | Likelihood | Mitigation |
|------|:---:|---|
| Companion feels annoying (too frequent) | Medium | Adaptive interval. Users who dismiss 3x in a row → auto-pause. Engagement tracking. |
| Companion hallucinates urgency | Medium | Urgency from deterministic context (email count, deadlines), not LLM. LLM only decides nudge vs skip. |
| Privacy concern (reading emails in background) | Low | V1: email metadata only (from, subject, timestamp, unread count). No body access. Local context builder. |
| User ignores companion (notification fatigue) | Medium | No engagement in 24h → reduce to every 2 hours. 3 days → auto-pause. |
| Companion memory leaks personality data | Low | Separate SQLite store, user-local. OS disk encryption. Never leaves machine. |
| **NEW: Workspace activity summaries are stale** | Medium | Summaries are cached per cycle. If user is actively working, summaries lag ≤15 min. Acceptable for V1. Future: real-time workspace activity events. |
| **NEW: Personal workspace leaks into companion context** | Low | Companion context builder uses its own logic (does NOT go through `_get_workspace_context()` which suppresses personal). But companion should treat "Personal" like any other workspace — report its activity, just don't privilege it. |
| **NEW: Multiple schedulers for multiple users** | Low | V1: single user. V2 when multi-user: each scheduler runs independently, shared nothing. |

---

## 9. Competitive Differentiation (Updated)

| Feature | EA Companion | OpenClaw | Claude Code Desktop | Perplexity | Codex |
|---------|:---:|:---:|:---:|:---:|:---:|
| Background/proactive | **✅ Context-aware** | ⚠️ Basic heartbeats | ⚠️ Routines (code only) | ❌ | ❌ |
| Multi-project workspace | **✅ Full isolation** | ❌ Single flat ws | ❌ Per-repo sessions | ✅ Space-scoped | ⚠️ Project=repo |
| Warm personal rapport | **✅ Companion personality + memory** | ⚠️ SOUL.md persona | ❌ | ❌ | ❌ |
| Email/task triage | **✅ Metadata + nudges** | ✅ Through tools | ❌ | ❌ | ❌ |
| Tool execution | ✅ (via workspace opt-in) | ✅ | ✅ | ❌ | ✅ |
| Rich desktop UI | **✅ Flutter + timeline feed** | ❌ CLI + menu bar only | ✅ Desktop app | ❌ Web only | ✅ Desktop app |
| Local-first (privacy) | **✅ Local model default** | ✅ Self-hosted | ❌ Cloud-dependent | ❌ Cloud only | ✅ Local |
| Skills ecosystem | ✅ (global skills) | ✅ ClawHub marketplace | ✅ Plugin + skills | ❌ | ❌ |

**The gap EA Companion uniquely fills:** No product combines proactive check-ins + workspace awareness + personal warmth + rich desktop UI + local-first privacy. OpenClaw has warmth and proactivity but no workspaces. Claude Code Desktop has rich UI and proactivity (routines) but no warmth and no cross-project awareness. EA Companion sits at the intersection.

---

## 10. V2 & Beyond

| Capability | V1 | V2+ |
|-----------|:---:|------|
| Tool calls | ❌ No tools | Companion can execute workspace tools with user opt-in (per invocation) |
| Triggers | Timer only | API trigger (your CI/CD can notify companion), GitHub events (PR merged → companion knows), calendar events |
| Cloud execution | ❌ (local only) | Companion routines run in cloud VM (like Claude Code Routines) |
| Mobile companion | ❌ | Flutter mobile app with companion feed |
| Multi-user | Single user | Per-user schedulers. Team/enterprise companion that checks in on team progress |
| Voice | ❌ | Voice companion on macOS (like OpenClaw voice wake) |
| Push notifications | ❌ (in-app only) | OS-level push for urgent nudges when app is backgrounded |

---

## 11. Success Metrics

| Metric | Target |
|--------|--------|
| Companion check-in engagement (action vs. dismiss) | >60% action rate |
| Daily companion runtime | >8 hours (user's work day) |
| Average cost per user per day | <$0.50 (local model) or <$2.00 (cloud model) |
| False urgency rate (flagged urgent, user disagrees) | <20% |
| Companion opt-in rate | >80% of EA users within 1 month |
| Companion-related support issues | <1/week |
| **NEW: Workspace awareness accuracy** (companion correctly identifies which workspace an event belongs to) | >95% |
| **NEW: User satisfaction with companion tone** (via dismiss patterns — do they engage warmly or dismiss coldly?) | <30% cold dismiss rate |

---

## 12. Decision Points for Peer Review (Updated)

1. **Companion personality:** Should the companion have a name? OpenClaw users name theirs ("Claudia," "Brosef," "Shelly"). Names create attachment but also expectations. "EA" is neutral. **Recommendation:** No name in V1. Let users name it if they want, but don't force a persona.

2. **Check-in frequency:** 15 min default, adaptive. More frequent = more useful but risks annoyance. OpenClaw heartbeats seem to be hourly-ish based on user tweets — hourly is too infrequent for an active workday companion. **Recommendation:** 15 min, adaptive down to 5 min during high activity, up to 30 min during quiet.

3. **Proactive email sending:** Start conservative. Flag only. Draft-with-approval in V2.

4. **Workspace tool execution:** e.g., "You have 3 overdue tasks in Q2 Planning. Want me to open that workspace and help you work through them?" Yes — but explicit user opt-in per invocation. Never autonomous.

5. **Privacy model:** Metadata-only by default. Full content access requires explicit per-account opt-in.

6. **Toast vs. no toast:** Keep toast in V1. It's 55 lines. If it's annoying, turn it off in V1.1. Better to have it and learn than not have it and never know.

7. **NEW: Should companion report "personal" workspace activity?** Yes. The runner suppresses personal workspace context to avoid polluting the system prompt with unnecessary metadata. But the companion operates at the user level — "personal" is a valid workspace. Treat it equally.

8. **NEW: Companion memory scope vs. workspace memory scope.** Companion memory is ONLY for user personality facts (preferences, habits, important contacts). It does NOT store project facts (budgets, deadlines, tasks). Those belong in workspace memory. Clear separation prevents contamination.

9. **NEW: Adaptive interval learning.** The scheduler should learn from dismiss/engage patterns. If user dismisses 3 consecutive nudges, increase interval. If user engages (clicks "Switch to Q2"), decrease interval. This is ~10 lines of logic in `_next_interval()`.

---

## 13. Verdict — Should We Build This?

**Yes.** Five reasons, updated from the original three:

1. **Market signal is undeniable.** OpenClaw at 367k stars proves massive demand for personal AI assistants. Claude Code Desktop at millions of devs proves the desktop agent form factor works. The proactive companion is the next obvious evolution — and OpenClaw users are already asking for smarter heartbeats.

2. **EA already has the infrastructure.** The workspace system (521 tests, fully built as of April 30) supplies the exact multi-project context the companion needs. The SDK (AgentLoop, providers, tools, middleware, memory) requires **zero changes** to support a companion. The companion is ~180 lines of new backend + ~300 lines of Flutter — on top of a battle-tested 16,000-line SDK.

3. **Differentiation is defensible.** There is currently **no product** that combines proactive check-ins + cross-workspace awareness + warm companion personality + rich desktop UI + local-first privacy. EA Companion defines a category.

4. **Workspaces make the companion smarter than any competitor.** Because EA has proper project isolation, the companion can say "You have 3 overdue tasks in Q2 Planning, but Pipeline is green" instead of "You have 3 overdue tasks" (which could be anything). Workspace context makes nudges precise.

5. **V1 is deliberately minimal.** No tool-calling loops, no complex prompt engineering. One job: assess pre-computed context across all workspaces and output "[SKIP]" or a 1-2 sentence nudge. This makes it cheap, fast, and safe to experiment with tone before adding complexity.

---

## Appendix A: Competitive Timeline

| Date | Event | Relevance |
|------|-------|-----------|
| Apr 2026 | OpenClaw public release (as Clawdbot). 405pts on HN. | Proves demand for personal AI assistants. "Heartbeats" introduced as proactive check-in primitive. |
| Apr 2026 | OpenClaw renames from Clawdbot. Reaches 367k stars. | Community growth validates the "personal assistant" category beyond HN hype. |
| Apr 2026 | Claude Code Desktop launches Routines (schedule + trigger + GitHub events). | Proves "autonomous scheduled agent" is a shipping feature in a major product. |
| Apr 2026 | EA implements workspace isolation (521 tests, 15 files changed). | EA now has the infrastructure to support cross-workspace companion awareness. |
| **May 2026** | **EA Companion proposal (this document).** | **First proactive, workspace-aware, local-first executive AI companion.** |

---

## Appendix B: Post-Review Revisions (May 1, 2026)

**Revision 2 — Workspace alignment + competitive deep-dive + UI/UX reimagining.**

Changes from Revision 1 (initial post-review):

1. **Workspace-aware context builder.** `_build_context()` now calls `list_workspaces()` and summarizes per workspace. Previously used a flat user-level summary. Workspace activity now includes "personal" workspace (companion treats it equally — unlike runner.py which suppresses personal context).

2. **Companion memory gets a real location.** Follows DataPaths pattern: `data/users/{user_id}/companion/memory.db`. Added confidence scoring and decay (0.01/day). Sources tracked: `inferred`, `explicit`, `learned`.

3. **Notification store gets `workspace_id`.** Enables per-workspace filtering for inline nudges (Surface 2) and workspace badges in the feed (Surface 4). Index on `(workspace_id, dismissed)` for fast per-workspace queries.

4. **Companion AgentLoop explicitly NOT in `_loop_cache`.** The loop cache is keyed by `user_id:workspace_id`. Companion is user-global. Managed by `CompanionScheduler` directly.

5. **Full competitive analysis.** Deep-dive into OpenClaw (architecture, heartbeats, skills, channel model), Claude Code Desktop (Routines, Cowork, cross-surface continuity), OpenCode, Perplexity Spaces, Codex Desktop. Market convergence analysis identifying the four-pattern intersection EA can own.

6. **UI/UX completely reimagined:**
   - Surface 1: `CompanionPulse` (animated breathing circle) replaces text-based `CompanionStatusBadge`. Visual presence without reading.
   - Surface 2: `CompanionContextPill` (compact contextual insert between messages) replaces `role == "companion_nudge"` message bubble variant.
   - Surface 3: `CompanionToast` refined with gesture dismiss (swipe up), amber warning color (distinct from accent), easeOutBack enter animation.
   - Surface 4: `CompanionFeed` redesigned as rich timeline with workspace badges, contextual actions ("Switch to Q2" not generic "Open"), acknowledgment actions ("Thanks" not just "Dismiss"), date-grouped sections with sticky headers.

7. **Lines updated:** Backend ~180 (from ~130), Flutter ~300 (from ~220). Total ~480 (from ~350). Richer feed accounts for the increase. Worth it.

---

## Appendix C: Competitive Reference — AI Agent Landscape (Updated)

| Category | Products | Maturity |
|----------|----------|----------|
| Voice Assistants | Siri, Alexa, Google Assistant, Bixby | Mature (billions of devices) |
| AI Chatbots | ChatGPT, Claude, Gemini, Pi, Replika | Mature (hundreds of millions DAU) |
| Coding Agents | Claude Code, Copilot, Cursor, OpenCode, Aider, Bolt | Early (millions of devs) |
| Personal AI Assistants | OpenClaw (367k stars), Khoj, AgentGPT, AutoGPT | Early (hundreds of thousands of power users) |
| Desktop Coding Apps | Claude Code Desktop, Codex Desktop, OpenCode Desktop (beta) | Early (released Q1-Q2 2026) |
| **Proactive Workspace AI** | **None** | **No products exist** |

EA Companion would be the first product in the "Proactive Workspace AI" category — and the first to combine all four market patterns (workspace isolation, background autonomy, companion warmth, rich desktop UI) into a single product.

---

*End of proposal.*
