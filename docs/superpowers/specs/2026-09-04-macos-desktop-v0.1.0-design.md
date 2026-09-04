# macOS Desktop v0.1.0 Design

> **Status:** Approved direction, pending peer review of the backend-impact memo and this design. This is a product/design specification, not an implementation plan.
>
> **Companion review:** `docs/architecture/macos-dmg-v0.1.0-backend-impact-review.md`

## 1. Goal

Ship **Assistant v0.1.0** as a signed, notarized, local-first macOS DMG. A non-technical single user drags the app to Applications, opens it, connects an API key or a local model server, and uses Assistant without Python, `uv`, Docker, or terminal setup.

The native app remains a native-rendered Zig/Native SDK client. The refresh evolves the existing Assistant visual system rather than introducing a web frontend or replacing the app's rendering architecture.

## 2. Product boundaries

### In scope

- macOS, local-first, one macOS user, one bundled loopback backend.
- Apple Silicon (`arm64`) release first.
- First-run provider setup: API key, local model server, or custom endpoint.
- Managed browser automation: a version-pinned, signed browser helper and compatible browser runtime ship inside the app, with no terminal or separate installation.
- Refreshed first-run, chat, sidebar, Settings, Tools, Skills, and Subagents surfaces.
- Existing dark/light themes, Geist typography, teal accent, translucent surfaces, and reduced-motion setting.
- Message context compaction transparency after successful summarization.

### Explicitly out of scope

- Multi-user, public hosting, remote-first setup, or multi-device sync.
- Bundled language models.
- Email, contacts, and todos.
- Automatic updates.
- Arbitrary runtime provider plugins.
- A browser/WebView rewrite.

## 3. Desktop runtime and data contract

### 3.1 Product command and ownership

`assistant` is the sole product CLI/entry point. `assistant-sdk` is removed from executable names, Docker commands, documentation, tests, release scripts, and Python distribution metadata rather than kept as a compatibility alias. The project remains deployment-first; this is product metadata, not a renewed PyPI publication plan.

The native app owns its bundled backend lifecycle. It launches a desktop sidecar on loopback, waits for authenticated readiness, then connects. It must never kill or attach to an arbitrary process that happens to listen on a known port.

Desktop mode resolves the effective identity server-side as `default_user`; the native client does not send a configurable `user_id` or workspace identity. This makes `~/Assistant/` the actual single-user root rather than a container for a client-controlled user directory.

### 3.2 Data and secrets

All durable user data is rooted at:

```text
/Users/<macOS-login>/Assistant/
```

with this v0.1.0 structure:

```text
~/Assistant/
  Messages/
  Files/
  Memory/
  Skills/
  Subagents/
  .versions/
  .system/
  Logs/
```

`Messages/` replaces `Conversation/`. Existing users receive a safe one-time migration; a conflicting pre-existing `Messages/` directory must fail safely rather than be merged automatically.

The migration recognizes both legacy single-user data at `~/Assistant/Conversation/` and data created by the pre-DMG native client under `~/Assistant/Users/native_sdk_chat/`. If exactly one legacy tree exists, desktop mode promotes it to the `default_user` root and renames `Conversation/` to `Messages/`. If root-level and `native_sdk_chat` data both exist, the launcher must stop before opening stores and offer an explicit recovery/export path; it must never merge the two trees heuristically.

Only the known `~/Assistant` user-data roots are migrated automatically. Legacy source-checkout configuration under relative `data/users/...` is not discovered by scanning the filesystem: desktop mode starts `.system/` settings fresh, restores provider credentials through first-run/Keychain, and may add an explicit user-selected import flow in a later release.

Provider credentials are not persisted in this tree. The native app keeps durable secrets in macOS Keychain, injects approved credentials only into the authenticated sidecar at launch, and the sidecar keeps them in memory for its process lifetime. The launcher sets the Native SDK log directory to `~/Assistant/Logs` so product logs do not silently split into macOS default application-support/log locations. The runtime rendezvous file and every log/API response must exclude secrets.

### 3.3 Desktop capability baseline

The desktop profile forces all `email_*`, `contacts_*`, and `todos_*` tools to `none` before tool registration. The excluded tool families are absent from model-visible tools, API catalog results for the desktop identity, UI controls, storage initialization, and background schedulers. In particular, desktop startup disables email synchronization.

Browser automation remains an enabled desktop capability. The product bundles a version-pinned `agent-browser` CLI and compatible browser runtime as signed nested helpers; Assistant exposes the capability through normal browser tools and never asks the user to install, configure, or version-match a CLI. Browser profile state lives under `~/Assistant/.system/browser/`; the runtime must use macOS Keychain-backed storage for browser-secret material and must respect ordinary tool approval/interrupt rules for consequential actions.

## 4. Navigation and information architecture

The sidebar preserves the existing dark structural rail and uses this order:

```text
Search chats                     [+ New chat]

Recent conversations
  <chat list>

────────────────────
Tools
Skills
Subagents
────────────────────
Settings                     Theme control
```

### Rules

- The compact **New chat** compose button sits beside Search. It has an accessible `New chat` label and does not occupy a full-width row.
- Conversations are the primary navigation; the utility destinations are grouped at the bottom, above Settings.
- **Tools** is a top-level page containing Built-in tools and Connections. It replaces the duplicate Tools section currently nested in Settings.
- **Skills** is a top-level page for the existing skill catalog and scope state.
- **Subagents** is a top-level page for agent definitions, running jobs, progress, and outcomes.
- **Settings** contains Models, General, Appearance, privacy/data information, and recovery/help. It does not duplicate working-tool management.
- The app never renders email, contacts, or todos suggestions, navigation, storage status, or tool controls.

The existing `/tools`, `/skills`, and `/subagents` resource families become desktop-client contracts and must be available through the versioned API surface.

## 5. First-run experience

First-run occupies the existing empty-chat canvas rather than opening a separate product shell. It uses the current visual language: `#050506` dark canvas, teal accent, Geist, hairline borders, translucent nested cards, and quiet sidebar chrome.

### 5.1 Launch states

1. **Starting Assistant** — native app launches the sidecar and awaits authenticated bootstrap/readiness. This is a short loading state, not a configuration screen.
2. **Make Assistant yours** — no model is configured. A centered setup card is the only primary task.
3. **Connected chat** — a validated provider/model is available; setup disappears and the normal empty-chat experience appears.

The setup card states:

- **Paste an API key** — the primary, focused field.
- **Use a local model** — explicit scan/selection for local servers.
- **Custom setup** — provider, optional base URL, API key when applicable, and model ID.

### 5.2 API-key setup

1. User pastes a key.
2. The client classifies only high-confidence provider formats locally. This does not transmit the secret.
3. A high-confidence result is shown and validated against that one provider only.
4. Ambiguous/unknown keys show explicit provider selection and custom setup.
5. An opt-in action, labeled **Check likely providers**, may be offered for ambiguous keys. Before it runs, the UI names every candidate and states that the key will be sent only to those reviewed endpoints. The user may remove candidates or cancel.
6. On successful validation, user selects a model, Keychain stores the credential, the native app injects it into the sidecar, and chat becomes available.

The app never silently tests a pasted key against multiple third-party providers.

### 5.3 Local model setup

The local-model screen offers:

- **Ollama** using its native API.
- **LM Studio** using an OpenAI-compatible endpoint.
- **OpenAI-compatible server** for llama.cpp, LocalAI, vLLM, Jan, and compatible implementations.
- **Custom endpoint** with editable URL and model ID.

The optional scan queries a fixed allowlist of loopback endpoints without a credential. It never scans remote/private network addresses, and it excludes Assistant's dynamically allocated sidecar port. The user confirms any detected endpoint/model before it becomes active.

## 6. Everyday chat experience

### 6.1 Empty connected chat

After setup, the first empty chat is quiet and centered:

- Heading: **What would you like to do?**
- Plain-language supporting text mentioning thinking, writing, organization, and files.
- Up to three text starter prompts: **Plan my day**, **Draft something**, and **Explore my files**.

Remove the redundant `YOUR ASSISTANT` eyebrow, email/contact prompts, and the separate icon-only quick-action row. Do not add an attachment control until file import is implemented as a complete interaction.

### 6.2 Composer

The existing single, glass-like composer surface remains the core interaction. Its normal status row is exactly:

```text
Provider · Model ▾ · <input tokens> in / <output tokens> out       Send
```

Example:

```text
Anthropic · Claude Sonnet ▾ · 1,240 in / 418 out                  Send
```

Rules:

- Provider and model are both visible. The model segment opens the picker.
- Input/output counts are passive telemetry.
- Do not show `live`, a green healthy dot, or a permanent backend/engine label in the healthy state.
- Replace Send with Stop while a stream is active, retaining control width so the row does not jump.
- Show connection state only when action is required: `Reconnecting…` or `Backend unavailable`, with a clear recovery action.

### 6.3 Context compaction transparency

Assistant has summarization/context-compression support. The UX must make a successful compaction visible **after completion**, not warn the user merely because a threshold has been approached.

When the backend emits a successful `context_compressed` event, insert a compact non-chat transcript event at that point in the conversation:

```text
Context updated · 46k → 9k tokens
Assistant compacted earlier messages to preserve this conversation.
```

Rules:

- Use the typed event's `before.estimated_tokens` and `after.estimated_tokens`; round compactly for display and omit the reduction values only if either count is unknown.
- This event is a disclosure/timeline item, not an assistant response bubble and not a modal/toast.
- It persists in the conversation's event history so a user reopening the chat can understand why earlier context changed.
- Do not expose the internal summary text in v0.1.0.
- A failed compression never renders the success event. It renders a clear actionable error only when the next response cannot proceed normally.
- The composer does not receive a permanent context-percentage meter. Context information is communicated through completed compaction events instead.

The backend already has a typed `context_compressed` event containing before/after `ContextSnapshot`s. The desktop client must parse those values rather than discard them and replace them with the current generic system sentence.

## 7. Motion and transitions

Motion is deliberately sparse because chat, navigation, and composition are high-frequency interactions. Existing motion token timings remain the vocabulary: fast **120ms**, normal **180ms**, slow **320ms**. Use opacity and transforms only; never delay input or animate layout dimensions.

### Approved motion

| Surface | Purpose | Motion | Reduced motion |
|---|---|---|---|
| First-ever setup | Prevent a jarring state change; rare delight | Heading, subtitle, then setup card: opacity + 6px rise + `0.98 → 1` scale, normal 180ms, 30–60ms stagger | Opacity-only, fast 120ms |
| Provider validation | State indication | Input state cross-fades into recognized/custom state with opacity + 4px rise, fast 120ms | Opacity-only, fast 120ms |
| Provider/model picker | Spatial consistency | Anchored to the model trigger; opacity + `0.98 → 1` scale, fast 120ms; exits toward the same origin | Opacity-only, fast 120ms |
| Buttons, New chat, sidebar rows | Immediate feedback | Pressed surface plus `scale(0.97)`, fast 120ms | Pressed color/opacity only |
| Completed context update | Prevent a teleporting transcript insertion | Opacity + 4px rise, fast 120ms, then static | Opacity-only, fast 120ms |

### Explicitly no motion

- Keyboard shortcuts, chat switching, or sidebar page navigation beyond a quick active-fill/color update.
- Token counters, streamed text, message bubbles, or background gradients.
- Bouncy sends, typing effects beyond real streamed output, animated health indicators in the healthy state, or permanent decorative motion.

A small active-work indicator remains valid only while tools/subagents are genuinely active; it communicates real state and freezes under reduced motion.

## 8. Error handling and accessibility

- Sidecar startup, provider validation, local-server detection, connection failures, and migration conflicts have inline human-readable recovery states.
- A backend mismatch is detected at bootstrap before streamed chat begins.
- Copy avoids exposing API keys, internal file paths beyond the documented `~/Assistant` root, or opaque provider errors.
- All icon-only controls have accessible labels/tooltips; New chat and send remain keyboard accessible.
- Reduced motion converts spatial motion to short opacity changes; it does not remove state feedback.
- Dark/light themes maintain the existing token system and preserve readable contrast over translucent surfaces.

## 9. Backend contract additions

The companion memo owns detailed backend review. This design requires:

1. Authenticated desktop sidecar bootstrap/readiness payload with server/API/stream protocol versions, desktop identity/capability profile, and data-root readiness.
2. Dynamic loopback port, per-launch token, ownership nonce, single-instance lock, and graceful lifecycle contract.
3. `Messages/` migration and data-root mapping before stores initialize.
4. Desktop-only disabled email/contact/todo capabilities and email scheduler.
5. In-memory sidecar credential injection backed by native Keychain storage.
6. A signed, version-pinned `agent-browser` helper and compatible browser runtime, with profile data rooted under `.system/browser/` and no user-facing installation path.
7. Explicit provider-validation candidates, strict secret redaction, and loopback-only local model discovery.
8. Versioned resource contracts for Tools, Skills, Subagents, Models, Settings, Connections, browser automation, and streamed context-compression events.
9. Durable/replayable context-compression event projection with before/after estimates for transcript display.

## 10. Acceptance criteria

A clean macOS account can install Assistant v0.1.0, complete first-run with an API key or a local model, restart it, and retain only the intended durable data under `~/Assistant`.

The user can:

- Start/search/switch chats from the compact sidebar.
- Reach Tools, Skills, Subagents, and Settings as distinct destinations.
- See provider, model, and input/output tokens in the composer without a redundant healthy-state label.
- See a compact, persisted context-update event after successful summarization, including before/after counts when known.
- Recover from unavailable sidecar/provider states without terminal instructions.
- Never see or enable email, contacts, or todos in the v0.1.0 product.
- Automate a browser task without installing, naming, or version-matching `agent-browser` themselves.

The DMG contains a signed, notarized native app and backend helper, needs no Docker/Python installation, and passes clean-machine installation and sidecar lifecycle tests.
