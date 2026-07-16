# Assistant — Intro Website Design

**Date:** 2026-05-25
**Status:** Approved design
**Framework:** Astro
**Target audience:** Non-technical general users
**Tone:** Professional & trustworthy

---

## 1. Site Structure

Two pages:
- **`/`** — Landing page (single scroll)
- **`/docs`** — Documentation (quickstart, guides, deployment)

Navigation: `Features | Docs | GitHub | Get Started`

---

## 2. Visual Identity

### Design System
Inherits from Flutter app design tokens (`EA`):
- **Background:** `#08090A` (near-black canvas)
- **Surface:** `#0E0F11` (cards, panels)
- **Text primary:** `#E6E6E6`
- **Text secondary:** `#9B9B9B`
- **Accent:** `#239766` (deep emerald)
- **Accent muted:** `#0D2B22`
- **Accent hover:** `#2EAD78`
- **Borders:** `#1F1F22` (subtle), `#2A2B2E` (default)
- **Font:** Inter (UI), Fira Code (code)
- **Spacing scale:** 4px base (2/4/8/12/16/20/28/40)
- **Radius scale:** 4/6/8/10/12px (max 12px for cards)

### Logo: "Connected"
Two dots — one solid emerald, one ring — connected by a line. The larger solid dot reaches toward the smaller ring dot. Metaphor: connection, relationship, an assistant that pays attention.

### Wordmark
"Assistant" in Inter 600, `#E6E6E6`, -0.012em letter-spacing.

---

## 3. Hero Section

**Headline:** "Your assistant. One that gets you."
**Tagline:** "Learns your preferences, your workflow, what matters to you. Gets smarter every conversation — while your data stays on your machine."
**Tag:** "Open Source · Self-Hosted"
**CTAs:** "Download for macOS" (primary emerald) + "View on GitHub" (outline)
**Below:** App screenshot placeholder (Flutter chat UI)

---

## 4. Features Section — "What it Does"

**Layout:** 2×2 grid, max-width 640px, centered.
**Section headline:** "Everything an assistant should do. Without the setup."
**Section sub:** "One conversation. Email, browser, memory, tasks — all connected."

4 cards (icon + title + description):

| Icon | Title | Description |
|------|-------|-------------|
| ✉️ | Manages your email | Connect Gmail, Outlook, or iCloud. Read, search, reply, send — all through conversation. |
| 🌐 | Browses the web | Opens pages, fills forms, logs into sites, extracts data. It does things, not just chat. |
| 🧠 | Remembers everything | Learns your preferences, your workflows, your corrections. Gets better every conversation. |
| ✅ | Tracks what matters | Todos, contacts, files, projects. Ask and it knows — no manual organization needed. |

Card styling: `#0E0F11` background, `#1F1F22` border, 8px radius, 20px padding. Icon in `#0D2B22` rounded box.

---

## 5. Differentiation Section — "How it's Different"

**Section headline:** "Not just another chatbot. An actual assistant."
**Section sub:** "Most AI tools reply to what you say. Yours remembers what you mean."
**Style:** 4 vertical pillar cards (positive claims with implicit contrast)

| Icon | Title | Description | Contrast |
|------|-------|-------------|----------|
| 🧠 | Knows you, not just your prompts | Learns your preferences, corrections, and workflows from every conversation. | Other assistants start fresh every time. |
| ⚡ | Does things, not just chat | Reads your email, browses websites, manages tasks, searches files. | Other assistants can only reply. |
| 🔒 | Your data stays yours | Runs locally. Per-user encrypted storage. No cloud training on your conversations. | Other assistants send your data to their servers. |
| 🔄 | Any model you want | Works with OpenAI, Anthropic, Google, Ollama — 4,000+ models. | Other assistants force you into one model. |

---

## 6. Trust Section

**Placement:** Footer band (closing argument before CTA)
**Headline:** "Built for you. Open for everyone."
**3 cards in a row:**

| Icon | Title | Description |
|------|-------|-------------|
| 📖 | Open source | MIT license. Every line of code on GitHub. Audit it, fork it, contribute. |
| 🔒 | Runs locally | Your data stays on your machine. No cloud. No training on your conversations. |
| 🔄 | Your model, your choice | OpenAI, Anthropic, Google, Ollama — 4,000+ models. No lock-in. |

---

## 7. CTA Section

**Headline:** "Start with an assistant that gets you."
**Sub:** "Free and open source. Runs on your machine. Connects to the model you choose."
**Buttons:** "Download for macOS" (primary) + "View on GitHub" (outline)

---

## 8. Footer

4-column grid:
- **Brand:** Connected logo + "Assistant" + tagline
- **Product:** Features, Docs, GitHub
- **Community:** Discord, Twitter, Contributing
- **Legal:** License (MIT), Privacy

Bottom bar: copyright + social links.

---

## 9. Competitive Positioning

EA occupies a unique space: it's the only open-source AI assistant that:
- Ships with built-in tools (email, browser, todos, files), not community plugins
- Learns from conversation over time (Observational Memory + MemoryMiddleware)
- Proactively checks in (Companion System)
- Works with any model (4,172+ via models.dev)
- Runs fully locally with per-user data isolation

No other framework (OpenClaw, Hermes, OpenHUMAN, LangGraph, CrewAI) offers this combination.

---

## 10. Logo: Connected — Final Spec

```
SVG spec:
- ViewBox: 0 0 56 56
- Larger dot: circle at (20, 28), r=8, fill=#239766
- Ring dot: circle at (36, 28), r=5, fill=none, stroke=#239766, stroke-width=2.5
- Connecting line: from (28, 28) to (31, 28), stroke=#239766, stroke-width=2.5, round caps
- The line starts at the edge of the solid dot and approaches the ring dot
```

Favicon version: Same SVG scaled to 16×16, viewBox cropped to 14×14 centered.

---

## 11. Full Landing Page Scroll Order

1. **Nav bar:** Connected mark + "Assistant" | Features Docs GitHub [Get Started]
2. **Hero:** Tag → Headline → Sub → Buttons → Screenshot
3. **Features (2×2 grid):** Section label → Headline → Sub → 4 cards
4. **Differentiation (4 pillars):** Section label → Headline → Sub → 4 pillar cards
5. **Trust (footer band):** Section label → Headline → 3 cards
6. **CTA:** Headline → Sub → Buttons
7. **Footer:** 4 columns → Bottom bar
