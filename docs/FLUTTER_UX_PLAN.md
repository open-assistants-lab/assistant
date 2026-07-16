# Assistant — Flutter UI/UX Plan

> Design system, navigation architecture, and screen specifications for the Flutter frontend.
> Inspired by Wise's design philosophy: **Clarity, Context, Conviction.**

---

## Design Philosophy

Three principles adapted from Wise:

| Wise Principle | Our Translation |
|---|---|
| **Fee before you commit** | Show what the agent will do before it acts. Which tools, estimated cost, time. |
| **Trust through transparency** | Every tool call visible in real-time. Show reasoning. Show confidence. Never hide what the agent is doing. |
| **Progressive disclosure** | Simple path first. Chat is the hero. Files, email, todos are one tap deeper. Settings are two taps. |

---

## Color System

### Palette

| Token | Value | Usage |
|-------|-------|-------|
| Background | `#FFFFFF` | Screen background. Wise is 60%+ white. |
| Surface | `#F5F5F7` | Cards, sections, elevated surfaces |
| Primary | `#1A1A2E` | Text, nav active state, headers |
| Accent | `#0D9488` | CTAs, active indicators, links, success highlights |
| Accent hover | `#0F766E` | Pressed/active accent state |
| Success | `#22C55E` | Connected status, confirmed actions |
| Warning | `#F59E0B` | Needs attention, pending approvals |
| Danger | `#EF4444` | Destructive actions, errors, disconnect |
| Text primary | `#111827` | Headlines, body text |
| Text secondary | `#6B7280` | Subtitles, descriptions |
| Text dim | `#9CA3AF` | Timestamps, muted labels |

### Why Teal

- **Purple** (`#7C3AED`) — every AI app uses it (Copilot, Gemini, Notion AI). Invisible branding.
- **Lime** (`#9FE870`) — Wise's signature. Using it looks derivative.
- **Teal** (`#0D9488`) — professional, distinctive, pairs cleanly with navy. Says "trusted assistant" not "trendy AI" or "fintech clone".

### Accent Rules

Accent appears **only** on:
- CTA buttons (Approve, Send, Save)
- Active tab indicator
- Success checkmarks and connection dots
- Links and interactive text

Everywhere else is neutral. This restraint is what makes Wise feel fluid.

---

## Typography

Inter only. Material Design 3 type scale for consistent hierarchy. No JetBrains Mono — tool calls use Inter Medium with a chip background instead.

| Role | MD3 Token | Size | Weight | Letter Spacing |
|------|-----------|------|--------|----------------|
| Screen title | headlineLarge | 32px | w400 | 0% |
| Section title | headlineMedium | 28px | w400 | 0% |
| Body | bodyLarge | 16px | w400 | 0.5% |
| Caption/timestamp | bodySmall | 12px | w400 | 0.4% |
| Tool call label | labelMedium | 12px | w500 | 0.5% |
| Key metric | headlineSmall | 24px | w400 | 0% |
| Button | labelLarge | 14px | w500 | 0.1% |
| Chip | labelSmall | 11px | w500 | 0.5% |

> **Note:** The original plan specified SemiBold/w600 for titles and 14px for body. The implementation uses MD3 tokens (w400 for headlines, 16px for body) for consistency with Material Design 3 conventions and better line-height ratios.

---

## Spacing & Layout

| Token | Value | Usage |
|-------|-------|-------|
| Screen edge | 24px | Mobile left/right padding |
| Between sections | 32px | Major screen section breaks |
| Between cards | 12px | Horizontal gap between cards |
| Card padding | 16px | Internal card padding |
| Component default | 16px | Default spacing between components |
| Text to component | 16px | After a text block, before next component |

---

## Border Radius

Wise's single biggest visual differentiator. Generous curves communicate friendliness.

| Component | Radius (mobile) | Radius (desktop) |
|-----------|-----------------|-------------------|
| Cards | 24px | 32px |
| Buttons | 16px | 16px |
| Chips/pills | 12px | 12px |
| Bottom sheets | 24px (top corners) | 24px |
| Input fields | 12px | 12px |
| Dialogs | 24px | 28px |

---

## Motion

Natural, quick, purposeful — not decorative. This is Wise's motion philosophy.

| Transition | Duration | Curve |
|-----------|----------|-------|
| Tool call expand/collapse | 200ms | easeOut |
| Smart greeting collapse | 300ms | easeOut |
| Screen push (mobile) | 300ms | CupertinoPageRoute |
| Screen replace (desktop) | 200ms | FadeTransition |
| Success celebration | 400ms | easeInOut |

**Reserve animation only for:**
- State transitions (loading → content, empty → populated)
- Success celebrations (task completed, email sent)
- Sheet presentation/dismissal

No bouncing, shimmer on loading, parallax, or decorative micro-interactions. Wise is calm.

---

## Navigation Architecture

### Desktop (>= 1024px): Three-Panel Layout

```
┌──────────┬──────────────┬──────────────────────────┐
│ Sidebar  │ Chat Panel   │     Content Panel        │
│ (240px)  │  (60%)       │       (40%)              │
│          │              │                          │
│ 📂 Worksp│ Connection   │  [selected tab content]  │
│ 🧠 Memory│              │                          │
│ 🧩 Skills│ Messages     │  Depends on active       │
│ 🤖 Subag│  ├You: Hello  │  sidebar tab:            │
│ ⚙ Settings│ ├Asst: Hi!  │  Workspace → file browser│
│          │  │           │  Memory → memory CRUD     │
│          │  │           │  Skills → placeholder     │
│          │  │           │  Subagents → placeholder  │
│          │  │           │  Settings → placeholder   │
│          │  [📎] Ask... │                          │
└──────────┴──────────────┴──────────────────────────┘
```

**Key principles:**
- **Chat panel on desktop is workspace-scoped with tabs** — each open workspace gets a chat tab. Multi-tab chat with `ChatTabProvider`.
- **Content panel renders sidebar selection** — nested GoRouter child routes.
- **Sidebar items**: Workspace, Memory, Skills, Subagents, Settings (no Home/Email/Tasks in desktop sidebar).
- **No collapsible sidebar or resizable panels** (simplified for V1).

### Mobile (< 1024px): 4 Bottom Tabs

```
┌─────────────────────────────────────┐
│  Home  │  Email  │  Tasks  │  More  │
└─────────────────────────────────────┘
```

| Tab | Content | Route | Status |
|-----|---------|-------|--------|
| **Home** | Full dashboard + chat | `/` → redirects to `/workspace` | **Implemented** |
| **Email** | Email list | `/email` | Placeholder |
| **Tasks** | Tasks grouped by priority | `/tasks` | Placeholder |
| **More** | Catch-all | `/more` | Placeholder |

**Mobile Home Tab = Dashboard + Chat Combined:**

- `SmartGreeting` — time-aware "Good morning, Eddy" + date
- `StatusCards` — horizontal scrollable cards (unread emails, due tasks, active subagents)
- `QuickActions` — action chips: "Draft reply", "Summarize", "Schedule"
- Conversation section with full chat messages (WebSocket streaming)
- Persistent `ChatInput` at bottom
- Custom scroll with `SliverAppBar`

Fully implemented, not a placeholder. Status cards show live data (when backend available).

### Desktop Chat Panel = Workspace-Scoped Tabs

The desktop chat panel shows the active workspace's conversation with multi-tab support:

- Tab bar for each open workspace conversation
- `ConnectionBanner` showing connection status
- Message list with `MessageBubble` (user + assistant)
- `StreamingBubble` with trailing spinner during active streaming
- `ToolCallCard` for tool invocations (expandable)
- HITL `ApprovalSheet` (modal bottom sheet for destructive tool approvals)
- `ErrorBar` for errors
- `ChatInput` at bottom

### Desktop Sidebar Items (Current)

| Item | Route | Status |
|------|-------|--------|
| Workspace | `/workspace` | **Implemented** (file browser + SSE sync) |
| Memory | `/memory` | **Implemented** (full CRUD + search) |
| Skills | `/skills` | Placeholder |
| Subagents | `/subagents` | Placeholder |
| Settings | `/settings` | Placeholder |

> **Note:** The original plan specified Home, Files, Email, Todos, Contacts, Skills, Subagents, Memory, and Settings in the sidebar with a collapsible icon mode for tablet. The current implementation is more focused — Workspace (files), Memory, and placeholders. Home, Email, and Tasks exist only as mobile bottom tabs. No tablet layout (only 2 breakpoints vs planned 3).

---

## Screen Specifications

### 1. Chat Flow (Pushed from Home or Standalone)

When user taps a conversation or starts typing on Home, push to full-screen chat:

```
┌──────────────────────────────────────┐
│  ← Back          Assistant │
├──────────────────────────────────────┤
│                                      │
│  You: Draft a reply to Sarah         │
│                                      │
│  Assistant:                          │
│  I'll search your emails...          │
│                                      │
│  ┌─ email_search ────────────────┐  │  ← Collapsible tool card
│  │  query: "Sarah"               │  │     (Wise fee breakdown pattern:
│  │  ✓ Found 3 emails             │  │      tap to expand args/result)
│  └────────────────────────────────┘  │
│                                      │
│  Here's the draft:                   │
│  ┌────────────────────────────────┐  │
│  │ Hi Sarah,                      │  │  ← Editable draft card
│  │ I've reviewed the Q3 numbers.. │  │     (Wise "edit before send")
│  └────────────────────────────────┘  │
│  [Send]  [Edit]  [Discard]          │
│                                      │
├──────────────────────────────────────┤
│  [📎]  Message...            [➤]   │
└──────────────────────────────────────┘
```

### 2. HITL Approval Sheet (Full-Screen Bottom Sheet)

Destructive actions get a dramatic, unmissable confirmation. This is the Wise "Confirm Transfer" pattern.

```
┌──────────────────────────────────────┐
│  ⚠️ Approve Action                   │
├──────────────────────────────────────┤
│                                      │
│  The assistant wants to:              │
│                                      │
│  ┌──────────────────────────────────┐│
│  │ 📧 email_send                   ││  ← Tool icon + name (immediate clarity)
│  │                                  ││
│  │ To: sarah@company.com            ││  ← Key args highlighted
│  │ Subject: Re: Q3 Budget           ││     (Wise shows recipient + amount
│  │                                  ││      prominently)
│  │ [View full args ▾]              ││  ← Progressive disclosure
│  │   body: "Hi Sarah, I've..."      ││
│  │   attachments: []                ││
│  └──────────────────────────────────┘│
│                                      │
│  ┌── Risk Level ──────────────────┐ │
│  │ ⚠️  This will send an email    │ │  ← Destructive = amber warning
│  │    that cannot be undone.       │ │     (Wise shows exchange rate risk)
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─ Edit Before Approving ─────────┐ │
│  │                                  │ │
│  │ To: sarah@company.com            │ │  ← EDITABLE fields
│  │ Subject: [editable]             │ │     (Wise "edit transfer details")
│  │ Body: [editable]                 │ │
│  └──────────────────────────────────┘ │
│                                      │
├──────────────────────────────────────┤
│   [❌ Reject]          [✅ Approve]   │  ← Two clear buttons, Wise pattern
└──────────────────────────────────────┘
```

### 3. Email Screen

```
┌──────────────────────────────────────┐
│  📧 Email              [🔄] [⚙️]    │
├──────────────────────────────────────┤
│  ┌─ Connected Accounts ──────────┐  │
│  │  Gmail • sarah@company.com 🟢 │  │  ← Connection status (Wise's
│  │  Outlook • john@outlook.com 🟢│  │     connected bank accounts)
│  └────────────────────────────────┘  │
│                                      │
│  ┌─ Today ──────────────────────┐   │
│  │  🔴 Sarah Miller              │   │  ← Unread = red dot
│  │     Re: Q3 Budget Review      │   │
│  │     10:32 AM                  │   │
│  │                               │   │
│  │  🔴 David Chen                │   │
│  │     Project X deadline update │   │
│  │     9:15 AM                   │   │
│  │                               │   │
│  │     Mike Johnson               │   │  ← Read = no dot
│  │     Lunch Friday?              │   │
│  │     Yesterday                  │   │
│  └───────────────────────────────┘   │
│                                      │
│  Pull to refresh...                   │
│                           [💬]       │  ← FAB for contextual agent
└──────────────────────────────────────┘
```

Wise translations:
- **Connected accounts** = Wise's "linked bank accounts" (green dot = connected, red = disconnected)
- **Email list** = Wise's transaction list (grouped by date, relevance indicators)
- **Pull to refresh** = Wise's pull-to-refresh on transfer list

### 4. Tasks Screen

```
┌──────────────────────────────────────┐
│  ✅ Tasks                            │
├──────────────────────────────────────┤
│  ┌─ Today ──────────────────────┐  │
│  │  🔴 Review Q3 budget          │  │  ← Priority dots (red=high, amber=med)
│  │  🟡 Call accountant            │  │
│  │  🟢 Book flight to London      │  │
│  └────────────────────────────────┘  │
│                                      │
│  ┌─ This Week ──────────────────┐  │
│  │  🟡 Prepare board deck         │  │
│  │  🟢 Renew passport             │  │
│  └────────────────────────────────┘  │
│                                      │
│  ┌─ Completed ──────────────────┐   │
│  │  ✓ Send contract to David    │   │  ← Strikethrough, muted text
│  │  ✓ Schedule team sync         │   │
│  └────────────────────────────────┘  │
│                                      │
│  [+ Add Task]                        │
│                           [💬]       │  ← FAB for contextual agent
└──────────────────────────────────────┘
```

Wise translation: Scheduled payments screen — grouped by time, clear status indicators. Completed items visible but subdued.

### 5. Memory Panel (Implemented)

The Memory panel is implemented at `lib/features/memory/memory_panel.dart` (307 lines):

- Search bar using `AppSearchField`
- Memory list fetched via REST (`/memories/search` endpoint)
- Search support via query parameter
- Type badges for memory types (fact, preference, insight, event)
- Loading and error states
- Delete capability
- Reacts to workspace switching via `currentWorkspaceIdProvider`

### 6. Workspace Panel (Implemented)

The Workspace panel is implemented at `lib/features/workspace/workspace_panel.dart` (156 lines):

- File browser fetching from `/workspace/json` REST endpoint
- SSE sync listener on `/sync/stream` for real-time file changes
- File list with folders/files distinction and file sizes
- Loading and error states
- Reacts to workspace switching

### 5. Profile / Memory Screen (Original Plan)

```
┌──────────────────────────────────────┐
│  👤 Your Profile                     │
├──────────────────────────────────────┤
│  ┌─ What I Know About You ──────┐  │
│  │                                │  │
│  │  🏢 Work                       │  │  ← Grouped by domain (Wise's
│  │     Prefers dark mode          │  │     currency accounts)
│  │     Works in PST timezone      │  │
│  │     Uses Notion for notes      │  │
│  │                                │  │
│  │  📧 Communication              │  │
│  │     Prefers short emails       │  │
│  │     Formal tone with clients   │  │
│  │                                │  │
│  │  🔒 Private                    │  │  ← Explicit privacy indicators
│  │     Birthday: Dec 15           │  │     (Wise: fee transparency; we: data
│  │     Home address: [hidden]      │  │      transparency)
│  │                                │  │
│  └────────────────────────────────┘  │
│                                      │
│  [✏️ Edit] [🔍 Search memories]      │
│                                      │
│  ┌─ Confidence ─────────────────┐  │
│  │  ★★★☆☆ Prefers dark mode     │  │  ← Confidence stars (Wise's
│  │  ★★☆☆☆ Birthday Dec 15       │  │     transfer status badges)
│  │  ★★★★★ email: sarah@...      │  │
│  └────────────────────────────────┘  │
│                                      │
│  ┌─ Insights ───────────────────┐   │
│  │  "You tend to prioritize      │   │  ← AI-generated insights
│  │   urgent over important"      │   │     (Wise's market insights)
│  │                                │   │
│  │  [Dismiss] [Keep]             │   │  ← User can accept or reject
│  └────────────────────────────────┘  │
│                                      │
│  ────────────────────────────────    │
│  ⚙️ Settings                         │  ← Settings inside Profile
│  ┌────────────────────────────────┐ │
│  │ Connection: localhost:8080 🟢  │ │
│  │ Model: minimax-m2.5           │ │
│  │ Cost this session: $0.04      │ │
│  │                                │ │
│  │ Privacy                        │ │
│  │  Data stored locally ✓         │ │
│  │  Memory: On [Toggle]           │ │
│  │  Auto-approve: Read-only tools │ │
│  │                                │ │
│  │ Danger Zone                    │ │
│  │  [Clear conversation]          │ │
│  │  [Reset all memory]            │ │
│  │  [Disconnect all accounts]     │ │
│  └────────────────────────────────┘ │
└──────────────────────────────────────┘
```

### 6. Files Screen (Desktop Sidebar, Mobile More Tab)

```
┌──────────────────────────────────────┐
│  📁 Files                 ⟁ Remote │  ← Badge indicating server files
├──────────────────────────────────────┤
│  🔍 Search files...                  │
│                                      │
│  ┌─ Project X ──────────────────┐   │
│  │  📄 budget_q3.xlsx             │   │
│  │  📄 notes_meeting.md           │   │
│  │  📁 contracts/                  │   │
│  └────────────────────────────────┘   │
│                                      │
│  ┌─ Personal ──────────────────┐    │
│  │  📄 vacation_plans.txt         │   │
│  │  📄 tax_2025.pdf               │   │
│  └────────────────────────────────┘   │
│                                      │
│  Last synced: 2 min ago              │  ← Connectivity awareness
└──────────────────────────────────────┘
```

### 7. Subagents Screen (Desktop Sidebar, Mobile More Tab)

```
┌──────────────────────────────────────┐
│  🤖 Subagents               ● 2 active
├──────────────────────────────────────┤
│  ┌─ Researcher ──────────────────┐  │
│  │  Status: ● Running             │  │
│  │  Task: "Deep dive on Q3 market" │  │
│  │  Progress: ████████░░ 80%      │  │  ← Progress bar (Wise's transfer progress)
│  │  Cost: $0.02                    │  │
│  │                                │  │
│  │  [Instruct] [Cancel]           │  │
│  └────────────────────────────────┘  │
│                                      │
│  ┌─ Writer ─────────────────────┐  │
│  │  Status: ● Running             │  │
│  │  Task: "Draft board deck slides"│ │
│  │  Progress: ███░░░░░░ 30%      │  │
│  │  Cost: $0.01                    │  │
│  │                                │  │
│  │  [Instruct] [Cancel]           │  │
│  └────────────────────────────────┘  │
│                                      │
│  ┌─ Completed ──────────────────┐   │
│  │  ✓ Analyst — finished 1h ago  │   │
│  └────────────────────────────────┘  │
│                                      │
│  [+ New Subagent]                    │
└──────────────────────────────────────┘
```

### 8. Skills Screen (Desktop Sidebar, Mobile More Tab)

```
┌──────────────────────────────────────┐
│  🧠 Skills                           │
├──────────────────────────────────────┤
│  ┌─ Active ─────────────────────┐   │
│  │  🔬 deep-research             │   │  ← Tap to see full description
│  │     Deep research and analysis │   │
│  │     Loaded 2h ago              │   │
│  └────────────────────────────────┘   │
│                                      │
│  ┌─ Available ──────────────────┐   │
│  │  📋 planning-with-files       │   │
│  │     Multi-step task planning   │   │
│  │                                │   │
│  │  🔧 skill-creator              │   │
│  │     Create and improve skills  │   │
│  │                                │   │
│  │  🤖 subagent-manager           │   │
│  │     Create and manage subagents │   │
│  └────────────────────────────────┘   │
│                                      │
│  [🔍 Search skills...]               │
└──────────────────────────────────────┘
```

---

## Component Patterns

| Pattern | Wise Reference | Our Implementation |
|---|---|---|
| **Status indicator** | Green/amber/red dot next to balance | Green/amber/red dot next to connection status |
| **Card with disclosure** | Transfer amount → tap for fee breakdown | Tool call → tap for args/result |
| **Confirmation sheet** | "You're sending £X to Y — Confirm?" | "Send email to X about Y — Approve?" |
| **Progressive disclosure** | "Fee breakdown ▾" | "Show full args ▾" |
| **Grouped list** | Transactions grouped by date | Emails/tasks grouped by date/priority |
| **Action button** | Green "Confirm Transfer" with amount | Accent "Approve" with tool name |
| **Cancel/Destruct** | Gray "Cancel" secondary button | Gray "Reject" secondary button |
| **Pull to refresh** | Transfer list refresh | Email/task list refresh |
| **Error state** | "Transfer failed" with reason | "Action failed" with error + retry |
| **Empty state** | "No transactions yet" with CTA | "No emails — connect an account" with CTA |
| **Remote badge** | N/A | "⟁ Remote" on Files/Subagents (self-hosted) |
| **Sync status** | "Last updated 2m ago" on balances | "Last synced 2m ago" / "Offline — cached 10:32 AM" |

---

## Key UX Decisions

### 1. Chat is the Hero
Wise puts "Send Money" front and center. Our equivalent: the chat input. Always visible, always one tap away. On desktop, it's a permanent right panel. On mobile, it's the Home tab. The executive talks to their assistant — they don't browse it.

### 2. Show the Plan Before Acting
Before a destructive action, the approval sheet shows:
- What tool will be called
- Key arguments (recipient, subject, body)
- Risk level (read-only / destructive / irreversible)
- An "Edit before approving" section

This is the Wise "fee before you commit" principle. The "fee" is what the agent is about to do.

### 3. Tool Calls Are Inline, Not Hidden
Collapsed by default, expandable on tap. Like Wise shows the exchange rate and fee in the transfer flow. The user can always see what the agent is doing.

### 4. Smart Greeting = Wise Balance Summary
First thing on Home: status cards showing what needs attention NOW. Not chat history, not settings. The executive's "balance."

### 5. Editable Drafts = Wise "Edit Transfer Details"
When the agent drafts an email, the draft is EDITABLE. Never auto-send destructive actions.

### 6. Cost Transparency = Wise Transparent Fees
Settings shows "Cost this session: $0.04." Approval sheet shows "Estimated tokens: ~2,000."

### 7. Privacy-First Memory = Wise Data Protection
Memory profile shows what the agent knows, grouped by domain, with confidence indicators. Private info shown as "[hidden]" by default.

### 8. Desktop Side Panel for Chat, Not Separate Tab
On desktop, the agent is always beside you. Like VS Code's Copilot Chat. You don't switch away from your work to talk to the assistant — you talk while looking at your files/emails/tasks.

### 9. Mobile Tabs for Context, FAB for Agent
On mobile, the 4 tabs give context (Home/Dashboard, Email, Tasks, More). The FAB on Email/Tabs tabs provides contextual agent access. On Home, the chat input is always visible at the bottom.

### 10. Self-Hosted Awareness
In self-hosted mode, files and subagents are on a remote device. The UI shows "⟁ Remote" badges on Files, Subagents, and Skills. Lists show "Last synced: X min ago" or "Offline — cached from 10:32 AM." Queued messages send when reconnected (already implemented in WsClient).

---

## What We're NOT Building (Phase 1 Scope)

| Out of Scope | Why |
|---|---|
| Dark mode | Phase 2 — nail light mode first |
| Voice input/output | Phase 2 — complexity |
| Push notifications | Requires APNS/FCM — Phase 2 |
| Multi-language | Phase 2 |
| Tablet-specific layout | Phase 2 — responsive phone first, desktop second |
| Onboarding wizard | Settings in Profile tab is enough for MVP |
| Illustrations | Phase 2 — icons only for now |
| Dashboard analytics | Phase 2 — status cards first |
| Markdown rendering | Phase 2 — plain text in chat |
| Animated transitions | Phase 2 — currently NoTransitionPage |
| Nested routes | Phase 2 — routes are flat |
| Pagination | Phase 2 — lists load all items |
| Pull-to-refresh | Phase 2 — no refresh indicators |
| Emoji picker / attachments | Phase 2 — text-only chat input |

---

## Implementation Priority

### P0: Core (COMPLETE)

| Status | Priority | What | Why |
|--------|----------|------|-----|
| ✅ | **P0** | Design system (`app_theme.dart`, `app_colors.dart`, `app_typography.dart`, `app_spacing.dart`, `app_radius.dart`) | Everything depends on this |
| ✅ | **P0** | Responsive layout shell (desktop 3-panel + mobile bottom tabs via `LayoutBuilder`) | Core architecture |
| ✅ | **P0** | Home screen with smart greeting + status cards + quick actions + full chat | The 80% screen |
| ✅ | **P0** | HITL approval sheet (modal bottom sheet) | Safety-critical for destructive tools |
| ✅ | **P0** | GoRouter with shell route for navigation | Routing |
| ✅ | **P0** | WebSocket client with auto-reconnect + block-structured event handling | Agent connectivity |
| ✅ | **P0** | REST API client for all domains | Data fetching |
| ✅ | **P0** | AgentNotifier with ChatState + streaming support | Core state management |

### P1: Feature Screens

| Status | Priority | What | Why |
|--------|----------|------|-----|
| ✅ | **P1** | Desktop sidebar navigation (Workspace, Memory, placeholders) | Desktop experience |
| ✅ | **P1** | Desktop chat panel (workspace-scoped, multi-tab) | Desktop experience |
| ✅ | **P1** | Memory panel (full CRUD + search + workspace-aware) | Implemented early |
| ✅ | **P1** | Workspace panel (file browser + SSE sync) | Implemented early |
| ✅ | **P1** | Workspace management (create/switch/delete via REST) | Multi-workspace support |
| ✅ | **P1** | Chat tab provider (multi-tab workspace conversations) | Desktop chat |
| ✅ | **P1** | Test instrumentation framework + 13 test files | Quality |
| 🔲 | **P1** | Email screen (list + thread view) | Second most-used screen |
| 🔲 | **P1** | Tasks screen (grouped by priority/date) | Third most-used screen |
| 🔲 | **P1** | Mobile More tab (Contacts, Files, Skills, Subagents, Memory, Settings list) | Mobile navigation |

### P2: Extended Features

| Status | Priority | What | Why |
|--------|----------|------|-----|
| 🔲 | **P2** | Skills screen | Lower frequency |
| 🔲 | **P2** | Subagents screen (progress bars, instruct, cancel) | Lower frequency |
| 🔲 | **P2** | Settings screen (connection, privacy, danger zone) | Lower frequency |
| 🔲 | **P2** | Contacts screen | Lower frequency |
| 🔲 | **P2** | Editable drafts | High UX value, complex |
| 🔲 | **P2** | Cost tracking display (usage events consumed but not displayed) | `TODO(phase-14)` |

### P3: Polish + Phase 2

| Status | Priority | What | Why |
|--------|----------|------|-----|
| 🔲 | **P3** | Dark mode | Phase 2 |
| 🔲 | **P3** | Tablet-specific layout (collapsed icon sidebar + slide-in chat) | Phase 2 |
| 🔲 | **P3** | Push notifications | Phase 2 |
| 🔲 | **P3** | Voice input/output | Phase 2 |
| 🔲 | **P3** | Onboarding wizard | Phase 2 |
| 🔲 | **P3** | Markdown rendering in chat messages | Phase 2 |
| 🔲 | **P3** | Emoji picker / attachments in chat input | Phase 2 |
| 🔲 | **P3** | Nested routes (`/settings/profile`, `/email/compose`) | Phase 2 |
| 🔲 | **P3** | Animated transitions (currently `NoTransitionPage`) | Phase 2 |
| 🔲 | **P3** | Pull-to-refresh on panels | Phase 2 |
| 🔲 | **P3** | Pagination on list views | Phase 2 |
| 🔲 | **P3** | Accessibility labels beyond Material defaults | Phase 2 |

### Architecture Deviations from Original Plan

| Area | Original Plan | Current Implementation |
|------|--------------|----------------------|
| Breakpoints | 3 (mobile 768, tablet 1024) | 2 (mobile < 1024, desktop >= 1024) |
| Routes | Nested (`/more/contacts`, `/more/files`) | Flat (all top-level) |
| Route transitions | CupertinoPageRoute + FadeTransition | `NoTransitionPage` (no animations) |
| Models | Freezed + json_serializable | Plain Dart classes with manual `fromJson`/`copyWith` |
| Desktop sidebar items | Home, Files, Email, Todos, Contacts, Skills, Subagents, Memory, Settings | Workspace, Memory, Skills, Subagents, Settings |
| Home screen | Phase 15 (placeholder in Phase 13) | Phase 13 fully implemented with dashboard |
| Memory panel | Phase 17 | Phase 13 implemented |
| Workspace panel | Not in original plan | Phase 13 implemented |
| State management | `StateNotifier<ChatState>` | Same, plus `WorkspaceNotifier`, `ChatTabNotifier` |

---

## Flutter Implementation Notes

> **Last updated:** Phase 13 implementation complete. Architecture reflects current state.

### State Management
Currently: Riverpod with `StateNotifier<ChatState>` via `AgentNotifier` (513 lines). Also `WorkspaceNotifier` (74 lines) and `ChatTabNotifier` (49 lines).
To add: `AsyncNotifier` for email/tasks/memory (data lives on server, not in WS stream).

### Routing
Using `go_router` ^14.8.1 with:
- Root redirect `/` → `/workspace`
- `ShellRoute` wrapping all main routes in `ResponsiveShell`
- `NoTransitionPage` on all shell routes (no animations)
- Top-level `/chat` route (outside shell)
- 2 navigator keys: `_rootNavigatorKey`, `_shellNavigatorKey`
- `EaRouteObserver` for navigation instrumentation

Routes implemented (flat, no nesting):
```
/            → redirect to /workspace
/workspace   → WorkspacePanel (file browser)
/email       → PlaceholderScreen('Email')
/tasks       → PlaceholderScreen('Tasks')
/contacts    → PlaceholderScreen('Contacts')
/memory      → MemoryPanel (full CRUD)
/skills      → PlaceholderScreen('Skills')
/subagents   → PlaceholderScreen('Subagents')
/settings    → PlaceholderScreen('Settings')
/more        → PlaceholderScreen('More')
/chat        → ChatScreen (full-screen, outside shell)
```

### Desktop Layout (≥ 1024px)
Three-panel via `DesktopLayout` (337 lines):
- **Left sidebar** (240px): workspace list with create dialog, search bar, bottom nav items (Memory, Skills, Subagents, Settings). Uses `DesktopSidebarItem` enum.
- **Center chat panel** (60%): tab bar for workspace tabs, `ConnectionBanner`, message list with `MessageBubble`/`StreamingBubble`/`ToolCallCard`, HITL `ApprovalSheet`, `ErrorBar`, `ChatInput`.
- **Right content panel** (40%): nested GoRouter `child`.
- Sidebar width and chat/content split are hardcoded (no resizable panels, no collapsible icons).

### Mobile Layout (< 1024px)
Via `MobileLayout` (64 lines):
- Simple `Scaffold` with `BottomNavigationBar` (Home, Email, Tasks, More)
- Routes: `/`, `/email`, `/tasks`, `/more`

### WS Event Handling
All block-structured events implemented in `AgentNotifier`:
- `text_delta`, `text_start`, `text_end`
- `reasoning_delta` (events consumed, display deferred — `TODO(phase-13)`)
- `tool_input_start`, `tool_input_delta`, `tool_input_end`
- `tool_result`
- `interrupt` (triggers HITL ApprovalSheet)
- `usage` (events consumed, display deferred — `TODO(phase-14)`)
- `done`, `error`, `pong`

Backward-compat aliases: `ai_token→text_delta`, `tool_start→tool_input_start`, `tool_end→tool_result`, `reasoning→reasoning_delta`.

### WebSocket Client
`WsClient` (306 lines):
- `connect()`/`disconnect()` with auto-reconnect (5 attempts, exponential backoff)
- `sendMessage()` with offline buffering
- `approveToolCall()`, `rejectToolCall()`, `editAndApprove()`, `cancel()`, `ping()`
- 30-second heartbeat ping timer
- `ConnectionStatus` stream (disconnected/connecting/connected)
- `WsMessage` stream

### REST API Client
`ApiClient` (212 lines):
- Endpoints: `/health`, `/memories` (list + search), `/contacts` (list + add), `/todos` (list + add + update), `/email/messages`, `/skills`, `/conversation`
- `sendMessage()` for non-streaming messages
- `ApiException` class
- `workspace_id` parameter support

### Theme
All implemented at `lib/theme/`:
- `app_colors.dart` (31 lines): 18 color constants via `AppColors` class
- `app_typography.dart` (72 lines): MD3 type scale via `AppTypography` class, 8 text styles
- `app_spacing.dart` (12 lines): 8 spacing constants via `AppSpacing` class
- `app_radius.dart` (15 lines): 12 radius constants via `AppRadius` class
- `app_theme.dart` (153 lines): `AppTheme.light` — full Material 3 `ThemeData` combining tokens. Barrel export.

### Models (Plain Dart, no Freezed)
All models use manual constructors with `fromJson`:
- `ChatMessage` (74 lines): id, role, content, toolCalls, timestamp, metadata
- `ToolCallDisplay`: callId, toolName, args, resultPreview, isPending
- `WsMessage`: type, data
- `Memory` (30 lines): id, content, domain, memoryType, confidence, createdAt
- `Contact` (25 lines): id, name, email, phone, company
- `Todo` (27 lines): id, content, status, priority, createdAt

### Testing
- 13 test files across unit, widget, and integration tests
- `test_instrumentation.dart` (254 lines): singleton writing JSONL to stdout, in-memory capture mode
- `instrumented_app.dart` (130 lines): `InstrumentedApp` widget, `EaRouteObserver`
- Shared mocks: `MockWsClient`, `MockApiClient`, helper utilities

---

## Navigation Summary (Current Implementation)

| Platform | Pattern | Items |
|----------|---------|-------|
| **Desktop (>=1024px)** | Left sidebar (240px) + chat panel (60%) + content panel (40%) | Sidebar: Workspace, Memory, Skills, Subagents, Settings. Chat: workspace-scoped, multi-tab. |
| **Mobile (< 1024px)** | 4 bottom tabs | Home (chat+dashboard, implemented), Email (placeholder), Tasks (placeholder), More (placeholder) |
| **Desktop RHS (Chat)** | Persistent chat panel in center | Workspace-scoped with tab bar, ConnectionBanner, message list, streaming, tool cards, HITL approvals |
| **Desktop RHS (Content)** | Right panel (40%) | Renders GoRouter child: WorkspacePanel (files), MemoryPanel (CRUD), or placeholders |
| **Mobile cross-tab** | Dashboard + chat on Home tab | SmartGreeting, StatusCards, QuickActions, conversation section, ChatInput |
| **Tablet** | Not implemented | Original plan had 768–1024px with collapsed sidebar + slide-in chat. Deferred to Phase 2. |
