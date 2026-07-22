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
- A single canonical backend catalog contract for Settings.
- Native client storage capable of handling the returned catalog without hard-coded small caps.

Out of scope:

- Redesigning chat, sessions, tools, skills, or subagents.
- Adding billing/pricing management.
- Building account login flows beyond API key and environment-key status already supported by
  Settings endpoints.
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

Providers are ranked by credential status first, then name:

1. Providers with an existing user API key, environment key, or hosted key.
2. Providers without credentials.

Within each group, providers are sorted by normalized provider display name ascending. Normalization
is ASCII case-insensitive for v1.

Models inside each provider are sorted by normalized model display name ascending. Normalization is
ASCII case-insensitive for v1.

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

For the first implementation, load the full Settings catalog into the Native client and filter
locally. Do not ship the current hard limits of 32 providers, 64 models per provider, or 128 total
models as product behavior. The minimum supported catalog size for v1 is 128 providers and 8,192
models total. If bounded storage is still used, reaching the bound must show an explicit truncation
message and must be treated as a bug to fix before broad release.

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
- After save succeeds, update the provider's `key_source` to `user`, move it into the configured
  provider rank group, unlock every model row for that provider, preserve the current search text,
  and select the pending model.
- If the key saves but selecting the pending model fails, keep the key saved, restore the previous
  selected model, and show a non-blocking model-save error near the catalog header.
- If validation or saving fails, keep the modal open and show the error inline.
- While testing or saving, disable modal actions that would submit twice.

All Settings requests must use JSON serialization or explicit JSON string escaping. Do not build
request bodies for `api_key` or `default_model` with raw string interpolation.

Secret handling:

- Never render the raw key outside the focused input.
- Never log, persist in diagnostics, or include the key in screenshots/test artifacts.
- Clear `key_input`, `pending_provider_id`, `pending_model_id`, and `pending_model_name` on `Esc`,
  `Cancel`, and successful completion.
- If the Native SDK cannot mask text input yet, display obscured characters only and track native
  password-input support as a separate SDK follow-up before broad release.

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

Focus rules:

- Provider headers are not focusable; only model rows are focusable.
- Opening Settings focuses the search field and sets the active row to the first visible model.
- Changing search resets the active row to the first visible model and scrolls it into view.
- Arrow navigation wraps only if the existing app pattern supports wrapping; otherwise it clamps at
  the first and last visible rows.
- Opening the API-key modal moves focus to the key input.
- Closing the modal returns focus to search and preserves the active row for the pending model if it
  is still visible.

Arrow-key navigation is part of the target interaction, not a nice-to-have. If Native SDK event
limitations block it, create a tracked follow-up and keep search focus plus modal Enter/Esc working
in the first pass. Mouse-only model selection is not acceptable for this redesign.

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

The existing fixed-size Native settings arrays must not silently truncate the catalog. Prefer dynamic
storage for provider/model data. If bounded arrays are required by the Native SDK architecture, they
must meet the v1 minimum of 128 providers and 8,192 total models, and the UI must expose truncation
explicitly with a message that explains the limit.

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

Settings must use a dedicated canonical catalog endpoint:

- `GET /settings/model-catalog?user_id=X`

Do not reuse `/models` for this feature. The app currently has multiple `/models` handlers with
different contracts, so a Settings-specific endpoint avoids route and response ambiguity.

Required Settings catalog response:

```json
{
  "providers": [
    {
      "id": "agnes",
      "name": "Agnes",
      "key_source": "env",
      "has_key": true,
      "models": [
        {
          "id": "agnes:agnes-2.0-flash",
          "name": "Agnes 2.0 Flash",
          "provider": "agnes",
          "provider_display": "Agnes",
          "key_source": "env"
        }
      ]
    }
  ]
}
```

`key_source` values are:

- `hosted`: available through the app without user-managed credentials.
- `user`: stored user API key.
- `env`: configured outside the UI through environment/deployment settings.
- `none`: no usable credentials.

`key_source` must be derived by one shared backend helper used by both `GET /settings` and
`GET /settings/model-catalog`. The helper should classify providers in this order:

1. `hosted` when the deployment intentionally exposes a backend-managed shared provider key to this
   user experience.
2. `user` when the current user has a stored API key.
3. `env` when the provider is configured by environment/deployment settings but is not exposed as
   hosted.
4. `none` otherwise.

If Agnes or another provider is backed by an environment key but presented as the built-in hosted
experience, the helper must consistently return `hosted` in both Settings responses.

The endpoint must include unconfigured providers and their known models so locked rows can be shown
and unlocked from the catalog. If a provider is known but model metadata is unavailable, include the
provider with an empty model list and let the UI show `No models available` under that provider.

Provider/model source of truth:

- Include providers that the app can validate, store, and select through the current provider factory
  and Settings API.
- Include each provider's known models from the model registry or provider-specific curated list.
- Exclude providers that cannot be validated or selected by this app yet.
- If product wants to show unsupported models.dev providers later, add an explicit `unsupported`
  state and non-actionable rows in a separate spec.

Other existing Settings endpoints remain:

- `GET /settings?user_id=X` returns default model and provider key status.
- `GET /settings/model-catalog?user_id=X` returns the canonical catalog response above.
- `POST /settings/test-key` validates a proposed provider key.
- `POST /settings/api-keys` stores a validated user key.
- `PATCH /settings` saves the selected default model.

If a server-side search fallback is needed, define it before implementation with query parameters,
response shape, total counts, and ordering guarantees. Do not mix client-side truncation with search,
because that makes provider/model discovery unreliable.

## Error States

- Settings load failure: show a compact error with Retry.
- Models load failure: show a compact error in the catalog area with Retry.
- No models for a provider: show `No models available` only under that provider when it matches the
  current filter.
- API key validation failure: keep modal open and show the returned error.
- API key save failure: keep modal open and show the returned error.
- Model save failure: restore previous selection and show a non-blocking error near the catalog
  header.
- Current model missing from catalog: show `Current: <model id> (not in catalog)` and let the user
  select any available model to recover. Do not clear the existing setting until a replacement model
  is successfully saved.

## Acceptance Criteria

- Settings shows one long provider-grouped model catalog, not provider cards.
- Locked-provider key entry is a centered modal layered over Settings, not inline provider-card
  expansion.
- Opening the modal transfers focus to the key input; closing it restores focus according to the
  focus rules above.
- Providers with credentials appear before providers without credentials.
- Providers and models sort alphabetically inside their rank groups.
- Search filters by provider and model name case-insensitively.
- Clicking a configured model saves it as the default model.
- Clicking a locked model opens the API-key modal.
- Successful key test/save selects the originally clicked model automatically.
- Failed key test/save leaves the modal open with an error.
- The UI remains compact and usable with many models.
- Keyboard basics work: search typing, modal Enter/Esc, and row navigation. If Native SDK event
  limitations block row navigation, the implementation must include a tracked follow-up and still
  support search focus plus modal Enter/Esc.
- The catalog is not silently truncated by existing Native provider/model array limits.
- The Settings catalog endpoint has one documented response contract used by the Native client.

## Verification

Use Native and backend checks:

- `cd native-sdk-experiment && native test`
- `cd native-sdk-experiment && native build`
- Targeted API tests for `/models` and Settings endpoints if backend behavior changes.
- `native automate` smoke test covering search, model selection, locked-model modal, failed key,
  cancel, and successful key save if a safe test provider/key path is available.
