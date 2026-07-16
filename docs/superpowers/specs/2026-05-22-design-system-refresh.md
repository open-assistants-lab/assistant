# Design System Refresh — Linear-inspired with Deep Emerald

**Status:** Draft — pending user review
**Date:** 2026-05-22
**Owner:** UX / Frontend

---

## Goal

Transform the Flutter app from "Material Design defaults with custom colors" into a cohesive, premium product with the precision and restraint of Linear, anchored by a Deep Emerald accent.

The current app uses light grey as its "accent" (`#D4D4D4`), which gives it no visual identity. Every screen reads as generic. This refresh fixes the foundation (color, type, spacing, motion) then rebuilds the chat surface, then polishes components.

## Non-Goals

- Restructuring information architecture (navigation, panels, screens stay where they are)
- Adding new features
- Light-mode redesign (we keep light mode functional but optimize dark mode first; light mode gets a parallel palette derived from the dark one)
- Replacing Material widgets wholesale (we override theme; we don't rewrite Buttons/Inputs from scratch unless necessary)
- Custom icon set (we keep Material Symbols — they pair well with this aesthetic)

## Aesthetic North Star

**Linear** — engineering precision applied to a personal-assistant context.

Signature traits we adopt:
- Pure near-black canvas, calibrated grey scale, **one** bold accent used sparingly
- Inter typography, tight letter-spacing (`-0.011em`), small-caps labels
- 6/8/10/12px radii (never round)
- 1px borders for separation, no shadows in dark mode
- 150–200ms ease-out micro-interactions everywhere
- Press-state: scale to 0.97
- Keyboard-first ergonomics

## Tokens — The Foundation

All tokens live in `flutter_app/lib/theme/tokens/` and flow through `EaTokens` (the existing structure is sound; we replace values).

### Color (`tokens/colors.dart`)

**Dark mode (primary):**

| Token | Value | Use |
|---|---|---|
| `bgCanvas` | `#08090A` | Root background |
| `bgSurface` | `#0E0F11` | Cards, panels, raised surfaces |
| `bgElevated` | `#131416` | Hover states, dialogs, popovers |
| `bgField` | `#1A1B1E` | Input backgrounds |
| `textPrimary` | `#E6E6E6` | Body text |
| `textSecondary` | `#9B9B9B` | Subtitles, meta |
| `textTertiary` | `#6B6B6B` | Disabled, deep meta |
| `textInverse` | `#08090A` | Text on accent buttons |
| `accent` | `#239766` | Primary actions, focus, active state |
| `accentHover` | `#2EAD78` | Hover state of accent |
| `accentMuted` | `#0D2B22` | Accent backgrounds (user bubble fill) |
| `borderSubtle` | `#1F1F22` | Internal dividers, card borders |
| `borderDefault` | `#2A2B2E` | Visible borders, input borders |
| `borderAccent` | `rgba(35,151,102,0.4)` | Focused inputs, accent borders |
| `success` | `#3DBC83` | (Same hue family as accent for cohesion) |
| `warning` | `#E0A04B` | Warm amber, not yellow |
| `error` | `#E55B5B` | Confident red, not Material's harsh red |
| `info` | `#5B95E0` | Calm blue |

**Light mode (parallel):**

| Token | Value |
|---|---|
| `bgCanvas` | `#FAFAFA` |
| `bgSurface` | `#FFFFFF` |
| `bgElevated` | `#F3F3F3` |
| `bgField` | `#F0F0F0` |
| `textPrimary` | `#0E0F11` |
| `textSecondary` | `#5B5B5B` |
| `textTertiary` | `#9B9B9B` |
| `accent` | `#1B7A52` *(darker for AA contrast on white)* |
| `accentHover` | `#15633F` |
| `accentMuted` | `#E6F4ED` |
| `borderSubtle` | `#E8E8E8` |
| `borderDefault` | `#D4D4D4` |
| `borderAccent` | `rgba(27,122,82,0.4)` |

### Typography (`tokens/typography.dart`)

Font: **Inter** (already supported by Flutter; bundle as asset or use system fallback).

| Token | Size | Weight | Letter-spacing | Line-height | Use |
|---|---|---|---|---|---|
| `displayLarge` | 28px | 600 | -0.02em | 1.2 | Screen titles |
| `displayMedium` | 22px | 600 | -0.015em | 1.25 | Section headers |
| `titleLarge` | 17px | 600 | -0.012em | 1.3 | Card titles, dialog titles |
| `titleMedium` | 15px | 500 | -0.011em | 1.4 | List items, settings rows |
| `titleSmall` | 13px | 500 | -0.005em | 1.4 | Panel headers, tab labels |
| `bodyLarge` | 14px | 400 | -0.011em | 1.6 | **Chat body, primary reading text** |
| `bodyMedium` | 13px | 400 | -0.005em | 1.55 | Secondary content, descriptions |
| `bodySmall` | 12px | 400 | 0 | 1.5 | Tool cards, meta |
| `labelLarge` | 13px | 500 | -0.005em | 1.4 | Button labels |
| `labelMedium` | 11px | 600 | 0.04em | 1.3 | Small-caps tags (uppercase) |
| `labelSmall` | 10px | 600 | 0.1em | 1.3 | Section labels (uppercase) — "Assistant", "Tool call" |
| `code` | 12.5px | 400 | 0 | 1.5 | Monospace (SF Mono fallback) |

The `labelSmall` is the "Linear small-caps label" — used for AI role indicators, tool category badges, status tags.

### Spacing (`tokens/spacing.dart`)

Keep current 4px base unit; adjust the named scale for tighter rhythm:

| Token | Value | Use |
|---|---|---|
| `xxs` | 2 | Micro gaps |
| `xs` | 4 | Tight inline gaps |
| `sm` | 8 | Default inline gaps, button padding y |
| `md` | 12 | Component padding |
| `lg` | 16 | Card padding, section gaps |
| `xl` | 20 | Generous padding |
| `xxl` | 28 | Screen edges (was `screenEdge`) |
| `xxxl` | 40 | Section breaks |

### Radius (`tokens/radius.dart`)

Linear's exact scale:

| Token | Value | Use |
|---|---|---|
| `none` | 0 | Sharp edges |
| `xs` | 4 | Tags, chips |
| `sm` | 6 | **Buttons**, small inputs |
| `md` | 8 | **Cards, message bubbles**, inputs |
| `lg` | 10 | Panels, larger cards |
| `xl` | 12 | Dialogs, modals |
| `full` | 999 | Avatars, pills |

### Motion (`tokens/motion.dart`)

Replace whatever's there with Linear's interaction model:

| Token | Value | Use |
|---|---|---|
| `instant` | 0ms | No animation (reduce-motion) |
| `fast` | 120ms | Hover, focus rings |
| `base` | 180ms | **Standard transitions** (panels, dialogs) |
| `slow` | 280ms | Page transitions, large layout shifts |
| `pressScale` | 0.97 | Scale-on-press value |
| `curveStandard` | `Cubic(0.2, 0, 0, 1)` | Default ease-out (Linear's curve) |
| `curveEntrance` | `Cubic(0.16, 1, 0.3, 1)` | Springy entrance |
| `curveExit` | `Cubic(0.4, 0, 1, 1)` | Sharp exit |

## Chat Surface — The Heart

The chat is where 80% of the app's feel lives. We redesign it from the ground up.

### Layout

**User messages:**
- Right-aligned, max-width 75% of container
- Background `accentMuted` (`#0D2B22` dark, `#E6F4ED` light)
- Border `1px solid borderAccent` (subtle emerald glow)
- Radius `md` (8px)
- Padding `12px 16px`
- Text style: `bodyLarge` in `textPrimary`

**AI messages (flat, no bubble):**
- Left-aligned, max-width 85% of container
- No background, no border
- Padding `8px 0` (vertical breathing room only)
- Above the text: small-caps role label
  - Markup: small dot (`accent` color, 6px) + text "ASSISTANT" in `labelSmall` (uppercase, tracked)
  - Color: `textTertiary` for the text
- Text style: `bodyLarge` in `textPrimary`
- Markdown rendering: clean, no decorative borders on code blocks, just `bgField` background

**Streaming AI message:**
- Same as AI message, but the role label dot pulses (1.2s opacity 0.4 → 1.0 → 0.4 loop)
- Optional: blinking cursor at end of streaming text (1px wide, 14px tall, `accent` color)

**Reasoning block:**
- Collapsed by default, shown as `bgSurface` card with `borderSubtle` border
- Radius `md`, padding `12px 16px`
- Header: small-caps "REASONING" label + chevron, click to expand
- Body when expanded: monospace font, `textSecondary`, `bodySmall`

**Tool call cards:**
- Background `bgSurface`, border `borderSubtle`, radius `md`
- Compact header row: small accent icon + tool name (monospace) + status badge on right
- Expandable body for args/result (collapsed by default)
- Status badges:
  - Running: dot + "Running" in `accent`
  - Success: small check + "Done" in `textSecondary`
  - Error: small X + "Failed" in `error`

**Spacing between turns:** `lg` (16px) vertical gap.

### Streaming Animation

When AI text streams in:
- New characters fade in (opacity 0 → 1) over 100ms
- No character-by-character bounce or typewriter effect (that's gimmicky for an assistant)
- When streaming completes, the cursor fades out over 200ms

### Empty state

When a workspace has no messages:
- Centered, vertical layout
- Subtle accent dot (12px circle, `accent` color, 0.6 opacity)
- Below: subtitle in `textSecondary` — "Ask anything. I'm here to help."
- Below that: 3 suggestion chips in a row (e.g., "Summarize my emails", "What's on my calendar?", "Add a todo")
  - Chips: `bgSurface` + `borderSubtle` border, radius `sm`, `bodySmall`, padding `8px 12px`
  - On hover: border becomes `borderAccent`

### Input

- Background `bgField`, border `borderDefault`, radius `md`
- Focus state: border `borderAccent`, no shadow
- Send button: square (32x32), radius `sm`, `accent` background when input non-empty, `accentMuted` when empty
- Inline shortcuts row above input (when empty): small chips for "/" commands, model selector

## Component Polish — Beyond Chat

### Buttons

Replace Material defaults with three button variants:

1. **Primary** — `accent` background, `textInverse` text, padding `8px 14px`, radius `sm`
2. **Secondary** — transparent background, `borderDefault` border, `textPrimary` text
3. **Ghost** — no border, no bg, `textPrimary` text, hover `bgSurface`

All buttons:
- `labelLarge` typography (13px, 500 weight)
- Scale to 0.97 on press, 180ms `curveStandard`
- Focus ring: 2px `borderAccent`, offset 2px
- Disabled: 40% opacity

### Cards

- Background `bgSurface`
- Border `1px solid borderSubtle`
- Radius `md` (8px)
- Padding `lg` (16px)
- Hover (when interactive): border becomes `borderDefault`, 120ms transition

### Tabs (workspace tabs in chat panel)

- Active: `textPrimary`, `accent` 2px underline (instead of full background)
- Inactive: `textTertiary`, no underline
- Hover: `textSecondary`
- Padding: `8px 12px`, `titleSmall` typography
- Underline animates with `base` duration on tab switch

### Sidebar items

- Active: `bgSurface` background, `textPrimary`, accent 3px left bar
- Inactive: transparent background, `textSecondary`
- Hover: `bgSurface` background, `textPrimary`
- Padding: `10px 12px`, radius `sm`
- Icon size 18px, gap 10px

### Dialogs

- Background `bgElevated`
- Border `1px solid borderDefault`
- Radius `xl` (12px)
- No shadow in dark mode; use border for separation
- Backdrop: pure black 60% opacity
- Entrance animation: scale 0.96 → 1.0, opacity 0 → 1, 200ms `curveEntrance`

### Inputs

- Background `bgField`
- Border `1px solid borderDefault`
- Radius `md`
- Padding `10px 14px`
- Focus: `borderAccent` border, no shadow, 120ms transition
- Placeholder: `textTertiary`
- Disabled: 50% opacity

## Animations & Micro-interactions

Replace `staggeredEntry` (the current "messages slide in" animation) with simpler, more sophisticated motion:

- **New message arrival**: fade-in only (200ms, no translation)
- **Tool call expansion**: height + opacity, 180ms `curveStandard`
- **Panel open/close**: slide + fade, 200ms `curveEntrance` in, 150ms `curveExit` out
- **Hover transitions**: 120ms `curveStandard` on all interactive elements
- **Press states**: scale 0.97, 100ms `curveStandard`
- **Focus rings**: appear instantly (no transition), disappear with 150ms fade

**Reduced-motion**: when OS setting is on, all animations become 0ms.

## Transition Choreography

Motion personality: **Hybrid** — quiet & functional for utility transitions, expressive only for "moments." Two animation tiers govern every transition in the app:

- **Utility tier** (`base` = 180ms, `curveStandard`): high-frequency UI changes the user expects to be fast. Goal: feel instant but never jarring.
- **Moment tier** (`moment` = 280ms, `curveEntrance` for entrance / `curveExit` for exit): rare, intentional moments that deserve a touch of delight.

Add new motion tokens to `tokens/motion.dart`:

| Token | Value | Use |
|---|---|---|
| `moment` | 280ms | Tier-2 moments (dialogs, completion states) |
| `curveSpring` | `Cubic(0.34, 1.56, 0.64, 1)` | Slight overshoot for "moment" arrivals (dialogs, completion icons) |

### Tier 1 — Utility Transitions

#### Workspace switch (chat panel tabs)
- Duration: `base` (180ms)
- Style: crossfade only — old messages fade out (opacity 1→0, 100ms) while new messages fade in (opacity 0→1, 100ms) with 20ms overlap
- No translation, no scroll animation (jumpTo bottom is instant)
- Implementation: wrap message list in `AnimatedSwitcher` keyed by workspace ID, transitionBuilder uses `FadeTransition`

#### Sidebar navigation (top-level routes: Email / Workspace / Chat / Memory / Settings)
- Duration: `base` (180ms)
- Style: incoming page slides in 8px from the right + fades in; outgoing page fades out
- Implementation: `PageTransitionsTheme` on `ThemeData` using a custom `PageTransitionsBuilder`
- No "swipe back" gesture animations (desktop-first)

#### Chat panel workspace tabs (horizontal tab bar)
- Duration: `base` (180ms)
- Style: underline indicator slides horizontally between tabs (animated `AnimatedPositioned` or `AnimatedAlign`)
- Tab text color fades from `textTertiary` → `textPrimary` over the same duration
- No content sliding — the content area uses the workspace-switch crossfade above

#### Sidebar item hover / active state
- Duration: `fast` (120ms)
- Background fades in (`bgSurface`), left accent bar slides in from -3px to 0 with same duration
- Icon does NOT scale or shift

#### Panels (settings drawer, right-side workspace panels)
- Open: slide in from edge (right or left depending on origin) over `base` (180ms), `curveEntrance`
- Close: slide out same direction over `fast` (120ms), `curveExit`
- Background dim layer: fade `bgCanvas` to `rgba(0,0,0,0.4)` over same duration

#### Tool card expand / collapse
- Duration: `base` (180ms)
- Animates `height` (via `AnimatedSize`) and child opacity together
- Chevron icon rotates 180° on the same curve
- The card itself has no entrance animation when it first arrives (it's already there from streaming)

#### Hover transitions (buttons, cards, list items, inputs)
- Duration: `fast` (120ms), `curveStandard`
- Animates: background, border-color, text-color simultaneously
- No size changes on hover (only press)

#### Focus rings (keyboard navigation)
- Appear: instant (no animation) — keyboard users need immediate feedback
- Disappear: `fast` (120ms) fade-out
- Style: 2px `borderAccent` ring at 2px offset, radius matches the focused element + 2

#### Press states
- Duration: 100ms (faster than `fast` for tactile feel)
- Scale to `pressScale` (0.97), `curveStandard`
- On release: scale back over 100ms

### Tier 2 — Moment Transitions

#### Dialogs (create-subagent, settings-edit, confirmation prompts)
- Open:
  - Backdrop fades in (`base` 180ms, `curveStandard`)
  - Dialog: scale `0.96 → 1.0` + opacity `0 → 1` over `moment` (280ms) with `curveSpring` (slight overshoot to ~1.005)
- Close:
  - Dialog: scale `1.0 → 0.96` + opacity `1 → 0` over `fast` (120ms), `curveExit`
  - Backdrop fades out over `fast` (120ms)
- Backdrop click outside: same close animation

#### Approval / interrupt prompt (when AI requests permission for destructive action)
- Appears as a docked panel at the bottom of the chat
- Entrance: slide up 24px + fade in over `moment` (280ms), `curveSpring`
- Exit: slide down 24px + fade out over `fast` (120ms), `curveExit`
- Accent left-border (3px) draws in from top after the panel settles (additional 200ms, `curveStandard`)

#### Tool call completion (status changes from "Running" → "Done" or "Failed")
- The status pill on the right of the tool card crossfades to new content (`base` 180ms)
- A small check-icon or X-icon strokes in (path-draw animation, 250ms, `curveStandard`) **only on success/error** — running→success is the only animated transition; nothing animates when the card first appears running
- After completion, the card's left border briefly pulses with the success/error color (300ms, opacity 0→0.6→0)

#### Streaming message arrival
- The new AI message's container fades in (opacity 0→1) over 200ms with `curveStandard`, no translation
- The "ASSISTANT" small-caps label appears first; the role-dot pulses (opacity 0.4 → 1.0 → 0.4, 1.2s loop, `curveStandard`) while streaming
- Text characters fade in as they arrive (opacity 0→1, 100ms each)
- On completion: the role-dot stops pulsing and scales briefly (1.0 → 1.2 → 1.0, 200ms, `curveSpring`) — a tiny "done" gesture
- Reasoning text streams identically but inside a collapsed-by-default reasoning card; the reasoning card itself fades in on first reasoning token

#### Cost / usage update (token counter incrementing)
- When token count changes after a turn, the number flips upward with a slot-machine-style animation: old number slides down + fades out, new number slides up + fades in (total 240ms, `curveStandard`)
- Only animates on significant changes (≥1% of total budget); below threshold updates instantly

#### Error toast / notification
- Slide in from top-right + fade over `moment` (280ms), `curveSpring`
- Auto-dismiss: slide out + fade over `base` (180ms) after 4s (longer if user is hovering)
- Manual dismiss: same exit

#### Empty-state → first message
- When user sends their first message in an empty workspace, the empty-state content (dot, subtitle, suggestion chips) fades out over `base` (180ms)
- Their first user bubble fades in 80ms after the empty state starts fading (subtle stagger)

### Cross-cutting Rules

- **No two animations exceed `moment` (280ms)**. Anything longer feels slow.
- **Never animate more than 3 properties simultaneously** on a single element. Pick the most semantically important properties (usually opacity + one transform).
- **Color transitions** always use `fast` (120ms). Never animate color over `base` or longer — it reads as "loading."
- **List items** never animate their own position (no reordering animations). Add new items with fade-in only.
- **Scroll position changes** are always instant (`jumpTo`), never animated. Animated scrolls fight with user input.
- **Loading skeletons** pulse at 1.4s opacity 0.4→0.8→0.4 (longer than the streaming role-dot pulse to differentiate "loading" from "thinking").

### Reduced Motion

When OS reduced-motion setting is enabled:
- All `Duration` values from motion tokens become `Duration.zero`
- The role-dot pulse stops (becomes solid)
- Slot-machine token counter updates instantly
- Crossfades become hard cuts
- Implementation: read `MediaQuery.of(context).disableAnimations`, gate all animation calls

### Performance Budget

- Every animation must hit 60fps (16.7ms per frame) on a 5-year-old Mac
- Use `AnimatedBuilder` + `RepaintBoundary` for any animation that triggers parent rebuilds
- The streaming role-dot pulse uses a single `AnimationController` shared across instances
- Tool-card status-pulse uses one-shot animations that dispose after completion

## Implementation Plan (high-level — detailed plan written separately)

Three phases, each independently shippable:

### Phase 1 — Foundation (tokens + theme)
- Replace `tokens/colors.dart`, `tokens/typography.dart`, `tokens/spacing.dart`, `tokens/radius.dart`, `tokens/motion.dart`
- Update `AppTheme._build()` to wire new colors into Material ColorScheme
- Bundle Inter font as asset (if not using system)
- Visual smoke test: every screen still renders without breaking

### Phase 2 — Chat surface rebuild
- `MessageBubble` — split into `UserBubble` (with bubble) and `AssistantMessage` (flat)
- `StreamingBubble` — flat, with pulsing role-label dot
- `ReasoningBubble` — collapsed card with small-caps header
- `ToolCallCard` — new compact design with status badges
- Empty state with suggestion chips
- Input redesign
- Remove `staggeredEntry`, replace with simple fade-in

### Phase 3 — Component polish
- Button variants (Primary/Secondary/Ghost)
- Tab redesign (underline-based)
- Sidebar redesign (left-bar active state)
- Dialog redesign (no shadow, borders only)
- Input components across the app
- Verify every screen feels consistent

## Out of Scope (Tracked for Later)

- Custom illustrations / mascots
- Animated logo / loading screens
- Theme switcher UI (we keep dark default, light works but no user-facing toggle in this refresh)
- New onboarding flow
- Marketing surfaces (landing pages, etc.)
- Mobile (we optimize for desktop/macOS first; mobile inherits but isn't tuned)

## Success Criteria

- **Cohesion**: every screen visibly belongs to the same product family
- **Identity**: emerald accent is recognizable as "ours" without being overbearing
- **Density**: comfortable to read for 30+ minutes without eye strain (Balanced 14px proven by Notion/Claude.ai)
- **Polish**: no element feels like a default; everything has been considered
- **Performance**: no animation costs >16ms per frame; reduced-motion respected
- **A11y**: contrast ratios AA minimum for all text/UI pairs, AAA for body text in dark mode

## Risks

- **Inter font licensing**: Inter is OFL-licensed and free; bundling adds ~300KB to app size. Mitigation: use system fonts as fallback, lazy-bundle Inter.
- **Existing widget assumptions**: some components hard-code colors or sizes. Mitigation: Phase 1 ends with a smoke test pass; we fix breakages before Phase 2.
- **Light mode parity**: light mode is parallel but less battle-tested. Mitigation: explicitly tested in Phase 3, scoped as "functional, not polished" for this refresh.

## Open Questions

None — all major decisions resolved in brainstorming. Implementation specifics (per-widget) will be handled in the implementation plan.
