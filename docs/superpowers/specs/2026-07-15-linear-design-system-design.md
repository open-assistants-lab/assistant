# Linear-Like Design System for Native SDK App

> **Date:** 2026-07-15
> **Status:** Approved
> **Target:** `native-sdk-experiment/` — Native SDK desktop app (macOS)

## Overview

A full design system for the Assistant Native SDK app, inspired by Linear's dark workbench aesthetic with a teal accent. Covers: color tokens (dark + light auto-switching), radius tokens, typography scale, sidebar navigation, chat panel with message bubbles, composer, and HITL approval bar.

## Architecture

**Approach A: Token Overlay** — Custom `ColorTokens` struct in Zig with dark/light variants (radius tokens are static — same for both themes). Markup stays declarative; components reference token names (`background="surface"`, `foreground="accent"`, etc.). Theme switching via `tokens_fn` + `on_appearance`.

No markup templates or Zig view builders — the SDK's built-in token set is sufficient.

## Color Tokens

### Dark Theme

| Token | Value | Usage |
|-------|-------|-------|
| `background` | `#08090c` | App background |
| `surface` | `#111319` | Cards, message bubbles, sidebar |
| `surface_subtle` | `#0e1015` | Composer, inputs, hover states |
| `surface_pressed` | `#1a1d24` | Active nav, pressed states |
| `text` | `#f4f4f5` | Primary text |
| `text_muted` | `#8b8d98` | Secondary text, inactive nav |
| `border` | `#1d1e22` | Borders, dividers |
| `accent` | `#14b8a6` | Teal — active nav dot, links, focus |
| `accent_text` | `#042f2e` | Text on accent fills |
| `destructive` | `#f87171` | Reject, errors |
| `destructive_text` | `#1a0a0a` | Text on destructive |
| `success` | `#4ade80` | Approve, done |
| `success_text` | `#052e1a` | Text on success |
| `warning` | `#fbbf24` | Pending, caution |
| `warning_text` | `#1a1606` | Text on warning |
| `info` | `#60a5fa` | Info badges, streaming indicator |
| `info_text` | `#0a1628` | Text on info |
| `focus_ring` | `#14b8a6` | Focus ring (teal) |
| `shadow` | `#000000` | Shadow color |
| `disabled` | `#3a3d44` | Disabled controls |

### Light Theme

| Token | Value | Usage |
|-------|-------|-------|
| `background` | `#ffffff` | App background |
| `surface` | `#f9fafb` | Cards, message bubbles, sidebar |
| `surface_subtle` | `#f3f4f6` | Composer, inputs, hover states |
| `surface_pressed` | `#e5e7eb` | Active nav, pressed states |
| `text` | `#18181b` | Primary text |
| `text_muted` | `#71717a` | Secondary text, inactive nav |
| `border` | `#e5e7eb` | Borders, dividers |
| `accent` | `#0d9488` | Teal — active nav dot, links, focus |
| `accent_text` | `#ffffff` | Text on accent fills |
| `destructive` | `#dc2626` | Reject, errors |
| `destructive_text` | `#ffffff` | Text on destructive |
| `success` | `#16a34a` | Approve, done |
| `success_text` | `#ffffff` | Text on success |
| `warning` | `#d97706` | Pending, caution |
| `warning_text` | `#ffffff` | Text on warning |
| `info` | `#2563eb` | Info badges, streaming indicator |
| `info_text` | `#ffffff` | Text on info |
| `focus_ring` | `#0d9488` | Focus ring (teal) |
| `shadow` | `#000000` | Shadow color |
| `disabled` | `#d4d4d8` | Disabled controls |

### Theme Switching

- Use `tokens_fn` in `ChatApp.create()` options, returning a `*const ColorTokens` based on model state
- Model tracks `theme_mode: enum { dark, light, auto }` — `auto` follows system appearance
- `on_appearance` callback updates model when system theme changes
- Theme toggle (moon icon in sidebar) toggles between dark and light (no auto cycle — auto is the default, toggle switches to explicit dark/light)
- Light theme uses darker accent (`#0d9488` vs `#14b8a6`) for contrast on white

## Radius Tokens

| Token | Value | Usage |
|-------|-------|-------|
| `sm` | 8px | Buttons, badges, nav items |
| `md` | 12px | HITL bar, small cards |
| `lg` | 14px | Message bubbles, composer, sidebar |
| `xl` | 18px | App-level surfaces (window frame) |

## Typography

| Size | px / line-height | Usage |
|------|-------------------|-------|
| `size="sm"` | 11px / 1.4 | Role labels, badges, status text |
| default | 13px / 1.5 | Message content, nav items, body text |
| `size="heading"` | 28px / 1.2 | Panel headers (future) |
| `size="display"` | 48px / 1.1 | Hero stats (future) |
| `mono` span | 13px | Tool names, code references |

- Role labels: `size="sm"` + `foreground="accent"` for assistant, hidden for user
- Mono spans: `foreground="accent"` for tool names in HITL bar

## Layout

### App Structure

```
┌─────────────────────────────────────────────────────────────┐
│  OS Title Bar                                               │
├──────────┬──────────────────────────────┬───────────────────┤
│          │                              │                   │
│ Sidebar  │  Main Content Area           │  RHS Panel        │
│ 180px    │  (chat / tools / skills)     │  (canvas/files)   │
│          │  min 400px                   │  320px, collapsible│
│          │                              │                   │
│ New chat │  ┌──────────────────────┐    │  Placeholder for: │
│ ──────── │  │ Message list (scroll)│    │  • Canvas (HTML)  │
│ Chat 1   │  │                      │    │  • File preview   │
│ Chat 2   │  │                      │    │  • Code viewer    │
│ Chat 3   │  ├──────────────────────┤    │  • Image preview  │
│ ...     │  │ Composer             │    │                   │
│ ──────── │  └──────────────────────┘    │  WebView-based    │
│ Tools    │                              │  when active      │
│ Skills   │                              │  Hidden by default│
│ Subagents│                              │                   │
│ ──────── │                              │                   │
│ ⚙ Settings  ☾│                         │                   │
└──────────┴──────────────────────────────┴───────────────────┘
```

### Density: Comfortable

- Nav row height: 36px
- Gaps: 16px between sections, 8px within
- Padding: 16px around content areas
- Message bubble padding: 12px
- Composer border-radius: 14px (`radius="lg"`)
- Sidebar width: 180px

### Window

- OS title bar (standard macOS traffic lights)
- Window size: 1200x720 (min) — wider to accommodate RHS panel
- Two `<split>` elements: outer split (sidebar | main+rhs), inner split (main | rhs)
- RHS panel collapses to 0px width by default; expands to 320px when a canvas/file surface is active
- Main content area has `min-width` so it never shrinks below 400px

### RHS Panel (Canvas / Files Placeholder)

- **Hidden by default** — not visible until the agent or user opens a canvas/file surface
- Width: 320px when active, 0px when hidden
- Uses a second `<split>` between main content and RHS panel
- When active, contains a `WebViewSource` for rendering HTML canvas surfaces or file previews
- Placeholder content when no surface is active: centered text "No preview" in `foreground="text_muted"`
- Surfaces that can appear here (future):
  - **Canvas** — agent-generated HTML rendered in WebView
  - **File preview** — rendered file content (code, markdown, images)
  - **Code viewer** — syntax-highlighted code from tool results
- The panel is collapsible via a drag handle on the `<split>` divider
- Model tracks `rhs_surface: ?[]const u8` (null = hidden, "canvas" / "file" / "code" = shown)

## Sidebar

### Structure (top to bottom)

1. **New chat button** — full width, `background="surface_pressed"`, teal plus icon, 36px height, `radius="sm"`
2. **Chat list** — scrollable `<scroll>`, 32px min-height items, `radius="sm"`, gap 2px
   - Active: `background="surface_pressed"`, `foreground="text"`, teal dot (5px)
   - Inactive: `foreground="text_muted"`, no dot
   - Hover: `background="surface_subtle"`
   - Text: `size="sm"`, ellipsis overflow
3. **Bottom section** — `border-color="border"` top border, padding 8px
   - Tools, Skills, Subagents — 32px height, icon + label, `foreground="text_muted"`, hover `surface_subtle`
   - Bottom row: Settings (gear icon + "Settings" label) on left, theme toggle (moon icon only) on right

### Nav Item Icons

| Item | Icon |
|------|------|
| New chat | plus |
| Tools | wrench |
| Skills | lightning |
| Subagents | users |
| Settings | gear |
| Theme toggle | moon (dark) / sun (light) |

## Chat Panel

### Message Bubbles

**User messages:**
- `background="surface_subtle"`, `radius="lg"`, padding 12px
- Right-aligned, max-width ~72%
- No role label (position implies "You")

**Assistant messages:**
- `background="surface"`, `radius="lg"`, padding 12px, `border-color="border"`
- Left-aligned, max-width ~82%
- Role label: `<text size="sm" foreground="accent">Assistant</text>`

**System/error messages:**
- `background="surface"`, `foreground="text_muted"`, italic, `radius="md"`
- Centered, no border, `size="sm"`

**Streaming indicator:**
- `<badge variant="info">` with "Receiving..." text + pulsing dot
- Below the last assistant message while `streaming=true`

### Chat Switching Behavior

- When switching between chats, scroll to the **last message (bottom)** — not the previous scroll position
- Rationale: user switches to continue a conversation or check the latest response; bottom is where new content lives
- The model tracks `active_chat_id`; switching sets it and the scroll resets to bottom on next render
- New chats start empty with the composer focused

### Auto-Scroll During Streaming

- While tokens arrive, keep the message list pinned to bottom
- If user scrolls up during streaming, stop auto-scrolling (respect their reading position)
- Show a **jump-to-latest** button (floating, bottom-right of message list) when scrolled up
- Clicking jump-to-latest scrolls to bottom and resumes auto-scroll

### Load Older Messages

- When the user scrolls to the **top** of the message list, fetch older messages from the backend
- Backend endpoint: `GET /conversation/messages?user_id={user_id}&chat_id={chat_id}&before={oldest_msg_id}&limit=50`
- Insert fetched messages at the top, maintaining scroll position (don't jump)
- Implementation: track scroll offset in model via `<scroll on-scroll="scroll_changed">`; when `offset == 0` (scrolled to top), dispatch `load_older` Msg
- Show a subtle loading indicator at the top while fetching
- When no more messages remain, stop triggering (model tracks `has_older_messages: bool`)
- New chats start with most recent 50 messages; older ones load on demand

### Unread Badge

- Chat list items show a **teal dot + count** when new messages arrive in a chat that is not currently active
- Model tracks `unread_counts: [max_chats]u32` — incremented when a response completes for a non-active chat
- Badge: small teal circle with count, positioned right-aligned in the chat list item, `foreground="accent"`, `size="sm"`
- Badge disappears when the user switches to that chat (resets count to 0)
- Only shown for count > 0
- Backend signals new messages via SSE `done` event (response completed) — the client increments unread for the chat_id in the event payload

### Search Chat

- **Search input** at the top of the sidebar, below "New chat" button and above the chat list
- `<input text="{search_query}" placeholder="Search chats..." on-input="search_input_changed" />`
- Filters the chat list in real-time as the user types (client-side, no backend round-trip)
- Matches against chat title (first user message or derived title)
- When search is active, chat list shows only matching items
- When search is empty, full chat list is shown
- Clear button (x icon) appears in the input when text is present
- Keyboard: focus search with Cmd+F or `/` (future); Escape clears and blurs

### Markdown Rendering

- Assistant messages render as **markdown**: code blocks (with mono font + `surface_subtle` background), bold, italic, lists, links, blockquotes, inline code
- Use `<text>` with `<span>` children for inline formatting; code blocks use `<card background="surface_subtle" radius="md">` wrapper with `<text mono>` inside
- Links render in `foreground="accent"` and are clickable (future: `on-link` handler)
- The backend already sends markdown in `messages` events; the client renders it
- For MVP: render as plain text with mono spans for code blocks only; full markdown parsing is a follow-up

### Auto-Resize Composer

- The `<textarea>` in the composer grows with content from 1 line up to ~6 lines (~140px), then scrolls internally
- Single-line default: 32px height
- Grows by 20px per line up to max
- Send button stays vertically centered in the actions row regardless of textarea height

### Enter to Send

- Enter key sends the message (dispatches `send_message`)
- Shift+Enter inserts a newline (default `<textarea>` behavior)
- `<textarea on-submit="send_message" on-input="input_changed">` — `on-submit` fires on Enter in text fields

### Empty State

- When a chat has no messages, show a **welcome panel** instead of blank space:
  - Centered `<text size="heading">` "How can I help?" 
  - Subtitle `<text foreground="text_muted">` "Ask me anything, or try one of these:"
  - 3 suggestion buttons (ghost variant): "Triage my inbox", "Draft a weekly summary", "Find contacts in marketing"
  - Clicking a suggestion sends it as the first user message
  - Composer remains visible at the bottom, focused

### Composer

- `<input-group>` with `<textarea>` + `<input-group-actions>`
- `background="surface_subtle"`, `radius="lg"`, border `border-color="border"`
- Actions row: `<spacer grow="1"/>` + Stop button (ghost) + Send button (primary accent)
- Send enabled when not streaming; Stop shown while streaming (Send hidden or disabled)
- `placeholder="Type a message..."`

### HITL Approval Bar

- `<row background="surface" radius="md" padding="12">`
- Text: "Approve: " + tool name in `<span mono foreground="accent">`
- Buttons: Approve (`foreground="success"`) + Reject (`foreground="destructive"`, `variant="ghost"`)
- Appears between message list and composer when `has_pending=true`

## Future: Tablet & Mobile Responsive Tokens

Native SDK mobile is experimental. When it stabilizes, swap the token struct based on viewport width. No markup changes — only a `tokens_fn` conditional.

Three breakpoints: **Desktop** (>1024px), **Tablet** (600–1024px), **Mobile** (<600px).

| Property | Desktop | Tablet | Mobile | Reason |
|----------|---------|--------|--------|--------|
| Base font | 13px | 14px | 15px | Readability scaling |
| `sm` font | 11px | 12px | 13px | Minimum legible size |
| Nav row height | 36px | 40px | 44px | Touch target scaling |
| Button height | 36px | 40px | 44px | Touch target minimum (mobile) |
| `sm` radius | 8px | 7px | 6px | Proportional to screen |
| `md` radius | 12px | 11px | 10px | Same |
| `lg` radius | 14px | 13px | 12px | Same |
| `xl` radius | 18px | 17px | 16px | Same |
| Composer padding | 12px | 14px | 16px | Easier thumb typing |
| Nav icon size | 14px | 16px | 18px | Touch-friendly scaling |
| Sidebar width | 180px | 56px (icons) | 0px (hidden) | Space conservation |
| Nav label | shown | hidden (icon-only) | hidden (hamburger menu) | Progressive disclosure |
| Chat list | full | shown (icons expand on tap) | hidden (revealed via menu) | Progressive disclosure |

### Tablet adaptations (600–1024px)
- Sidebar collapses to **icon-only** (56px width) — labels hidden, icons centered
- Tapping a nav icon shows a tooltip/expands label inline
- Chat list items show truncated titles only, no preview text
- Composer stretches full width (no max-width on messages)
- Message max-widths increase to 88% (less horizontal whitespace)
- HITL bar and composer actions stay full width

### Mobile adaptations (<600px)
- Sidebar **hidden by default** — revealed via hamburger menu or swipe
- Chat list shown via overlay panel from left edge
- Composer **fixed to bottom** (safe-area aware)
- Message bubbles use full width (no max-width constraint)
- Nav items become a **bottom tab bar** (Chat, Tools, Skills, Settings) instead of sidebar
- Theme toggle moves into Settings panel
- HITL bar becomes a **bottom sheet** that slides up

### Implementation note
All three breakpoints use the same markup — only the `tokens_fn` conditional changes. The Native SDK's `tokens_fn` receives model state, so the model tracks `viewport_width` (updated via `on_resize` or a similar runtime callback) and returns the appropriate token struct. When the SDK stabilizes mobile, the sidebar collapse and bottom-tab-bar adaptations can be handled via `<if test="{is_tablet}">` / `<if test="{is_mobile}">` conditionals in markup.

## Testing Strategy

| Layer | Command | Verifies |
|-------|---------|----------|
| Unit tests | `native test` | Token struct compiles, dark/light switching, model logic |
| Markup check | `native check` | All token names resolve against ColorTokens/RadiusTokens |
| Automation smoke | `native automate assert 'role=button name="New chat"' 'role=textbox' 'role=button name="Send"'` | Sidebar, composer, buttons render |
| Visual screenshot | `native automate screenshot main-canvas` | Deterministic PNG regression |

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/theme.zig` | Create | `darkTokens`, `lightTokens`, `radiusTokens`, `tokens_fn` |
| `src/main.zig` | Modify | Wire `tokens_fn` into `ChatApp.create`, add theme model state |
| `src/app.native` | Rewrite | Full layout: sidebar + chat panel + composer + HITL bar |
| `src/tests.zig` | Modify | Add token switching tests, theme toggle tests |

## Out of Scope

- Tools, Skills, Subagents panel content (placeholders only for now)
- Settings panel content (gear button is placeholder)
- Canvas/editor surfaces (WebView — future)
- WebSocket protocol (using SSE + REST HITL)
- Mobile build target (experimental in Native SDK)