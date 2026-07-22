# Settings Model Catalog Design

## Goal

Redesign the Native SDK Settings model picker so the user chooses a model from one dense,
searchable catalog instead of managing provider cards. The interface should feel like a fast
desktop command surface: scan, search, select, and only handle API keys when a locked model is
chosen.

The current Settings UI exposes provider administration too prominently. This design makes model
selection the primary task and treats providers as grouping and authentication context.

## Scope

This spec covers the Settings model/provider selection experience in
`native-sdk-experiment/src/main.zig` and the backend data needed to populate it.

In scope:

- Provider-grouped model catalog.
- Search across provider names and model names.
- Ranking configured providers above unconfigured providers.
- Compact selectable model rows.
- Modal API-key unlock flow for locked providers.
- Keyboard behavior for search, list selection, and modal dismissal.

Out of scope:

- Redesigning chat, sessions, tools, skills, or subagents.
- Adding billing/pricing management.
- Building account login flows beyond API key status already supported by Settings endpoints.
- Client-side request cancellation, since the Native SDK does not currently expose fetch aborts.

## Layout

Settings opens as the existing transient panel from the sidebar. It has no Back button. Clicking
sessions, New chat, Tools, Skills, Subagents, or Settings again dismisses it.

The main content is a single model catalog:

```text
Settings

Model
Search providers and models...

Current: Agnes 2.0 Flash

AGNES                                    API key configured
  Agnes 2.0 Flash                       Selected
  Agnes 2.0 Pro
  Agnes 2.0 Mini

ANTHROPIC                                Key required
  Claude Sonnet 4.5                     Click to add key
  Claude Opus 4.1                       Click to add key

GOOGLE GEMINI                            Key required
  Gemini 2.5 Pro                        Click to add key
  Gemini 2.5 Flash                      Click to add key
```

Provider headers are lightweight separators, not cards. Model rows are compact line items, not
large buttons. The list should support hundreds or thousands of rows without visual bloat.

Appearance and About can remain below the model catalog, but they should not visually compete with
model selection.

## Ordering

Providers are ranked by configuration status first, then name:

1. Providers with an existing API key, environment key, hosted key, or logged-in account.
2. Providers without credentials.

Within each group, providers are sorted by provider display name ascending.

Models inside each provider are sorted by model display name ascending.

This keeps immediately usable models at the top while preserving predictable alphabetical ordering
for the rest of the catalog.

## Search

Search is case-insensitive and filters across both provider display names and model display names.

Search rules:

- Empty search shows all providers and all known models.
- If a provider name matches, show that provider and all of its models.
- If one or more model names match, show the provider and only the matching models.
- If both provider and model names match, provider-name match wins and all models are shown.
- Filtered results retain the same configured-first, provider-name, model-name ordering.
- No results shows a quiet empty state: `No providers or models found`.

Search should be fast enough to feel interactive while typing. If the model catalog becomes large,
the rendered list should be virtualized or otherwise bounded so filtering does not cause visible
lag.

## Model Row Behavior

Configured provider rows:

- Clicking a model selects it immediately.
- Selection calls `PATCH /settings` with the full model id, such as
  `{"default_model": "agnes:agnes-2.0-flash"}`.
- The selected row shows a checkmark or `Selected` label.
- If saving fails, the UI restores the previous selected model and shows a small error.

Unconfigured provider rows:

- Rows remain clickable, not disabled.
- Clicking a model opens the API-key modal for that model's provider.
- The selected locked model is remembered as the pending model.
- After the key is tested and saved successfully, the modal closes and the pending model is selected
  automatically.

Rows should not require a separate provider-level `Add key` button. The user expresses intent by
clicking the model they want.

## API-Key Modal

Clicking a locked model opens a small centered modal over the Settings panel.

Modal content:

- Title: `Add <Provider> key`.
- Supporting text: `Required to use <Model Name>.`
- Masked API key input.
- Show/Hide toggle for the masked key input. If the Native SDK cannot mask text input yet, keep
  the entered key visually obscured in app state wherever it is echoed and track SDK masking as a
  separate follow-up.
- `Cancel` button.
- `Test & Save` button.

Behavior:

- `Esc` closes the modal and preserves the current selected model.
- `Cancel` closes the modal and clears the typed key.
- `Enter` in the key field triggers `Test & Save`.
- `Test & Save` first calls `POST /settings/test-key`.
- If validation succeeds, save the key with `POST /settings/api-keys`.
- After save succeeds, refresh provider/model state as needed and select the pending model.
- If validation or saving fails, keep the modal open and show the error inline.
- While testing or saving, disable modal actions that would submit twice.

Environment-configured providers do not show remove-key behavior. They are ranked as configured and
their models can be selected, but their credentials cannot be deleted from the UI.

## Keyboard Interaction

The model catalog should be efficient without a mouse.

Required keyboard behavior:

- Typing in search filters the catalog.
- Arrow Down/Arrow Up moves the active row through visible model rows.
- Enter on a configured model selects it.
- Enter on a locked model opens the API-key modal.
- Esc clears search when no modal is open and search has text.
- Esc closes Settings when no modal is open and search is empty.
- Esc closes the API-key modal when it is open.

Arrow-key navigation is part of the target interaction, not a nice-to-have. If Native SDK event
limitations block it, document the blocker in the implementation notes and keep search focus plus
modal Enter/Esc working in the first pass.

## Data Model

The Native Settings state needs to represent models independently from provider cards.

Conceptual state:

```text
SettingsState
  visible
  loading
  search_text
  default_model_id
  providers[]
  models[]
  active_row_index
  pending_provider_id
  pending_model_id
  pending_model_name
  key_modal_visible
  key_input
  key_error
  key_testing
  model_save_error
```

Provider fields:

```text
ProviderInfo
  id
  display_name
  has_key
  key_source       hosted | user | env | none
```

Model fields:

```text
ModelOption
  id              provider:model_id or equivalent backend id
  display_name
  provider_id
  provider_name
  key_source
```

The UI derives visible groups from `providers`, `models`, and `search_text` rather than storing a
separate expanded/collapsed provider-card state.

## Backend Expectations

The existing endpoints are sufficient if they return complete provider/model metadata:

- `GET /settings?user_id=X` returns default model and provider key status.
- `GET /models?user_id=X` returns model id, model name, provider id/display name, and key source.
- `POST /settings/test-key` validates a proposed provider key.
- `POST /settings/api-keys` stores a validated user key.
- `PATCH /settings` saves the selected default model.

The model listing should include all known providers/models that the app can offer, not just the
providers currently configured with keys. If the full models.dev catalog is too large to ship to the
Native client all at once, the backend should still preserve the same ordering/filtering semantics
through pagination or server-side search.

## Error States

- Settings load failure: show a compact error with Retry.
- Models load failure: show a compact error in the catalog area with Retry.
- No models for a provider: show `No models available` only under that provider when it matches the
  current filter.
- API key validation failure: keep modal open and show the returned error.
- API key save failure: keep modal open and show the returned error.
- Model save failure: restore previous selection and show a non-blocking error near the catalog
  header.

## Acceptance Criteria

- Settings shows one long provider-grouped model catalog, not provider cards.
- Providers with credentials appear before providers without credentials.
- Providers and models sort alphabetically inside their rank groups.
- Search filters by provider and model name case-insensitively.
- Clicking a configured model saves it as the default model.
- Clicking a locked model opens the API-key modal.
- Successful key test/save selects the originally clicked model automatically.
- Failed key test/save leaves the modal open with an error.
- The UI remains compact and usable with many models.
- Keyboard basics work: search typing, modal Enter/Esc, and row navigation unless blocked by a
  documented Native SDK event limitation.

## Verification

Use Native and backend checks:

- `cd native-sdk-experiment && native test`
- `cd native-sdk-experiment && native build`
- Targeted API tests for `/models` and Settings endpoints if backend behavior changes.
- `native automate` smoke test covering search, model selection, locked-model modal, failed key,
  cancel, and successful key save if a safe test provider/key path is available.
