# Settings Panel Spec — Unified Provider/Model Picker

## Overview

Replace the current Settings panel (separate Default Model + API Keys sections) with a single unified provider/model picker. Users search, select a model, and manage API keys in one place — similar to opencode's provider|model selection.

## Entry / Exit

- **Open**: Click "Settings" in sidebar (same as now)
- **Close**: Click any sidebar element (New chat, a session, Tools, Skills, Subagents, Settings again). No dedicated back button. Settings is a transient panel — navigating away dismisses it.

## Layout

```
┌─────────────────────────────────────────┐
│ Settings                                │  ← header (title only, no back btn)
├─────────────────────────────────────────┤
│ [🔍 Search providers and models...]     │  ← search input, filters list
├─────────────────────────────────────────┤
│ ┌─────────────────────────────────────┐ │
│ │ Agnes                      ✓ Hosted  │ │  ← provider with key, expanded
│ │   • agnes-2.0-flash  ✓ selected      │ │  ← selected model (checkmark)
│ │   • agnes-2.0-pro                    │ │
│ │   • agnes-2.0-mini          Remove Key│ │  ← small text button
│ └─────────────────────────────────────┘ │
│ ┌─────────────────────────────────────┐ │
│ │ OpenAI                    ✓ Your key │ │  ← provider with user key
│ │   • gpt-5                            │ │
│ │   • gpt-4o                           │ │
│ │   • ...                    Remove Key│ │
│ └─────────────────────────────────────┘ │
│ ┌─────────────────────────────────────┐ │
│ │ Anthropic              Not configured│ │  ← provider without key
│ │   [ Add Key ]                        │ │  ← expand-on-click
│ └─────────────────────────────────────┘ │
│ ┌─────────────────────────────────────┐ │
│ │ Google Gemini           Not configured│ │
│ │   [ Add Key ]                       │ │
│ └─────────────────────────────────────┘ │
├─────────────────────────────────────────┤
│ Appearance                               │
│   Theme           [Switch to Light]      │
├─────────────────────────────────────────┤
│ About                                    │
│   Backend: http://127.0.0.1:8080        │
│   User: native_sdk_chat                  │
└─────────────────────────────────────────┘
```

## Ordering

Providers are sorted by key status:
1. **Providers with keys** (hosted, user, or env) — sorted alphabetically
2. **Providers without keys** — sorted alphabetically

This puts actionable providers (selectable models) at the top and setup-required providers at the bottom.

## Search

- Case-insensitive substring match on provider name AND model name
- If a model name matches, its provider is shown (even if provider name doesn't match)
- If provider name matches, all its models are shown
- Empty search = show all providers
- Search input has a clear (×) button

## Provider Card States

### 1. Provider with key (hosted/user/env)

- **Header row**: provider name (left) + status badge (right)
  - Status badge: "Hosted" (green), "Your key" (green), or "Env" (green)
- **Model list**: all models for this provider, each as a selectable row
  - Selected model: primary variant + checkmark icon
  - Unselected: secondary variant or ghost
  - Clicking a model immediately PATCHes `/settings` with `default_model`
  - No separate Save button
- **Remove Key**: small text button at bottom of card (ghost, destructive color)
  - For env keys: shows "Configured (env) — cannot remove" text instead of button
  - For user keys: clicking removes via `DELETE /settings/api-keys/{provider}`

### 2. Provider without key

- **Header row**: provider name (left) + "Not configured" badge (muted, right)
- **Body**: "Add Key" button (secondary)
- **On click Add Key**: expands inline with:
  - Key input field (masked, with Show/Hide toggle)
  - "Add" button (primary) + "Cancel" text button
- **On click Add** (auto-test flow):
  1. Call `POST /settings/test-key` with provider + key
  2. If valid: call `POST /settings/api-keys` to save → provider status updates to "Your key" → models appear
  3. If invalid: show error inline, key not saved, input stays for correction
  4. While testing: "Testing..." text, Add button disabled
- **On click Cancel**: collapses back to "Add Key" button, clears input

## Model Selection

- Clicking a model row immediately calls `PATCH /settings` with `{"default_model": "provider:model_id"}`
- Selected model shows checkmark + primary background
- The previously selected model reverts to normal style
- If PATCH fails: show error toast/text, revert selection

## Data Sources

### Backend endpoints (existing, no changes needed)

- `GET /settings?user_id=X` → `{default_model, provider_status: {provider_id: {name, has_key, key_configured_via_env}}}`
- `GET /models?user_id=X` → `{models: [{id, name, provider, provider_display, key_source, billing_mode}]}`
- `POST /settings/test-key` → `{valid: bool, error?: str}`
- `POST /settings/api-keys` → `{status: "stored"}`
- `DELETE /settings/api-keys/{provider}` → `{status: "removed"}`
- `PATCH /settings` → `{status: "updated"}`

### Native state

```
SettingsState {
    visible: bool,
    loading: bool,                    // fetching settings + models
    search_text: []const u8,
    default_model_id: []const u8,      // from /settings or updated on click
    providers: [max_providers]ProviderInfo,
    provider_count: usize,
    models: [max_models]ModelOption,  // from /models
    model_count: usize,
    // Per-provider transient state
    expanded_key_provider: usize,     // index of provider showing key input
    testing_provider: usize,          // index of provider being tested (-1 = none)
    testing: bool,
    test_error: []const u8,
    key_inputs: [max_providers][]const u8,  // per-provider key input text
    key_visible: [max_providers]bool,       // per-provider show/hide
    saving_model: bool,
    model_save_error: []const u8,
}

ProviderInfo {
    id: []const u8,
    name: []const u8,
    has_key: bool,
    via_env: bool,
}
```

## Sort + Filter Algorithm

```
fn filteredProviders(model, search) -> []ProviderIndex:
    1. Split providers into two groups: with_key, without_key
    2. Sort each group alphabetically by name
    3. If search is empty: return with_key ++ without_key
    4. If search non-empty:
       - For each provider, check if provider name matches search
         OR any of its models match search
       - Keep provider if either matches
       - If provider name matches: show all its models
       - If only models match: show only matching models
    5. Return filtered with_key ++ filtered without_key
```

## Auto-close

Settings panel closes when user:
- Clicks "New chat" button
- Clicks a session in the chat list
- Clicks Tools, Skills, or Subagents nav buttons
- Clicks "Settings" again (toggle behavior)

Implementation: add `model.settings.visible = false` to the handlers for `new_chat`, `switch_chat`, and the nav button press actions.

## Edge Cases

- **Models not loaded yet**: show "Loading..." in model list area
- **Provider has key but no models returned**: show "No models available"
- **Search with no results**: show "No providers or models found"
- **Network errors**: show error text in the relevant card, don't crash
- **Env-keyed providers**: can select models but cannot remove the key (it's in `.env`)
- **Multiple rapid model clicks**: last click wins; ignore stale PATCH responses