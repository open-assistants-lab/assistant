# Settings & Connectors Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolithic settings bottom sheet into a compact Settings dialog (server URL + model dropdown + about) and a full-screen Connectors modal (LLM Providers tab + Services tab), with backend API support for persistent provider key storage and default model management.

**Architecture:** Backend: new `settings.py` router with per-user JSON-based store for provider API keys and default model override. Flutter: `settings_screen.dart` rewritten as compact dialog; new `connectors_modal.dart` with two tabs; sidebar gets a connector icon; "Manage providers" link bridges Settings → Connectors.

**Tech Stack:** Python/FastAPI (backend), Flutter/Riverpod (frontend), SharedPreferences + new per-user JSON store (key persistence), ConnectKit vault (connector auth)

---

## File Structure

### Backend files:
- **Create:** `src/http/routers/settings.py` — new router for settings API
- **Modify:** `src/http/routers/__init__.py` — add `settings_router`
- **Modify:** `src/http/main.py` — include settings router
- **Modify:** `src/sdk/providers/factory.py` — check stored keys in `create_model_from_config()`

### Flutter files:
- **Create:** `flutter_app/lib/features/settings/providers/settings_provider.dart` — Riverpod providers for settings state
- **Modify:** `flutter_app/lib/features/settings/settings_screen.dart` — compact rewrite
- **Create:** `flutter_app/lib/features/connectors/connectors_modal.dart` — full-screen modal with tabs
- **Create:** `flutter_app/lib/features/connectors/widgets/llm_providers_tab.dart` — LLM Providers tab
- **Create:** `flutter_app/lib/features/connectors/widgets/services_tab.dart` — Services tab
- **Create:** `flutter_app/lib/features/connectors/widgets/provider_card.dart` — expandable provider card
- **Create:** `flutter_app/lib/features/connectors/widgets/connect_auth_form.dart` — dynamic auth form
- **Modify:** `flutter_app/lib/core/layout/desktop_layout.dart` — add connector sidebar icon + "Manage providers" bridge

---

### Task 1: Backend — Settings router with API key CRUD + default model

**Files:**
- Create: `src/http/routers/settings.py`

- [ ] **Step 1: Write the settings router**

```python
"""Settings API — per-user overrides for API keys and default model."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from src.app_logging import get_logger

logger = get_logger()
router = APIRouter(prefix="/settings", tags=["settings"])


def _settings_path(user_id: str) -> Path:
    from src.config.settings import get_settings
    root = get_settings().storage.data_dir or "data"
    return Path(f"{root}/users/{user_id}/settings.json")


def _read_settings(user_id: str) -> dict:
    path = _settings_path(user_id)
    if path.exists():
        return json.loads(path.read_text())
    return {"provider_keys": {}, "default_model": None}


def _write_settings(user_id: str, data: dict) -> None:
    path = _settings_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


@router.get("")
async def get_settings(user_id: str = Query("default_user")):
    """Read current settings (default model, which providers have keys)."""
    data = _read_settings(user_id)
    from src.sdk.registry import list_providers
    providers_meta = list_providers()
    provider_status = {}
    for p in providers_meta:
        pid = p.get("id", "")
        has_key = pid in data.get("provider_keys", {})
        has_env = _env_key_for_provider(pid) is not None
        provider_status[pid] = {
            "name": p.get("name", pid),
            "has_key": has_key or has_env,
            "key_configured_via_env": has_env,
        }
    return {
        "default_model": data.get("default_model"),
        "provider_status": provider_status,
    }


def _env_key_for_provider(provider_id: str) -> str | None:
    mapping = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "ollama": "OLLAMA_API_KEY",
        "ollama-cloud": "OLLAMA_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "together": "TOGETHER_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    env_var = mapping.get(provider_id)
    if env_var:
        import os
        return os.environ.get(env_var)
    return None


@router.patch("")
async def update_settings(
    body: dict,
    user_id: str = Query("default_user"),
):
    """Update settings (default_model, etc.)."""
    data = _read_settings(user_id)
    if "default_model" in body:
        data["default_model"] = body["default_model"]
    _write_settings(user_id, data)
    return {"status": "updated"}


@router.get("/api-keys")
async def list_api_keys(user_id: str = Query("default_user")):
    """List which providers have stored API keys (without revealing keys)."""
    data = _read_settings(user_id)
    keys = data.get("provider_keys", {})
    return {pid: bool(val) for pid, val in keys.items()}


@router.post("/api-keys")
async def set_api_key(
    body: dict,
    user_id: str = Query("default_user"),
):
    """Store an API key for a provider. Body: {provider: "...", api_key: "..."}."""
    provider = body.get("provider", "")
    api_key = body.get("api_key", "")
    if not provider or not api_key:
        raise HTTPException(400, "provider and api_key are required")
    data = _read_settings(user_id)
    data.setdefault("provider_keys", {})[provider] = api_key
    _write_settings(user_id, data)
    return {"status": "stored", "provider": provider}


@router.delete("/api-keys/{provider}")
async def delete_api_key(
    provider: str,
    user_id: str = Query("default_user"),
):
    """Remove a stored API key for a provider."""
    data = _read_settings(user_id)
    data.get("provider_keys", {}).pop(provider, None)
    _write_settings(user_id, data)
    return {"status": "removed", "provider": provider}
```

- [ ] **Step 2: Verify the router loads without errors**

Run: `uv run python -c "from src.http.routers.settings import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/http/routers/settings.py
git commit -m "feat(backend): add settings router with API key CRUD and default model"
```

---

### Task 2: Backend — Wire settings router into HTTP server

**Files:**
- Modify: `src/http/routers/__init__.py`
- Modify: `src/http/main.py`

- [ ] **Step 1: Add import and __all__ entry**

Add to `src/http/routers/__init__.py`:
```python
from src.http.routers.settings import router as settings_router
```

Add `"settings_router"` to `__all__`.

- [ ] **Step 2: Include router in main.py**

Add to `src/http/main.py` imports:
```python
    settings_router,
```

Add after `app.include_router(tools_router)`:
```python
app.include_router(settings_router)
```

- [ ] **Step 3: Verify server starts**

Run: `uv run python -c "from src.http.main import app; print(len(app.routes))"`
Expected: prints route count

- [ ] **Step 4: Commit**

```bash
git add src/http/routers/__init__.py src/http/main.py
git commit -m "feat(backend): wire settings router into HTTP server"
```

---

### Task 3: Backend — Check stored keys in provider factory

**Files:**
- Modify: `src/sdk/providers/factory.py`

- [ ] **Step 1: Read the current factory.py to understand create_model_from_config**

```bash
cat src/sdk/providers/factory.py
```

- [ ] **Step 2: Modify create_model_from_config to check settings store**

Add a `user_id` parameter and check stored keys before falling back to env vars:

```python
def create_model_from_config(
    config_model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    user_id: str = "default_user",
) -> LLMProvider:
```

In the function body, after the `provider_keys` dictionary is assembled from the `provider_keys` parameter, insert a check against the stored settings:

```python
    # Check per-user settings store for keys not provided via frontend
    if resolved_key is None or not resolved_key:
        try:
            settings_path = Path(f"data/users/{user_id}/settings.json")
            if settings_path.exists():
                stored = json.loads(settings_path.read_text())
                stored_keys = stored.get("provider_keys", {})
                if provider_type in stored_keys:
                    resolved_key = stored_keys[provider_type]
        except Exception:
            pass
```

Add `import json` and `from pathlib import Path` if not already present.

- [ ] **Step 3: Verify tests still pass**

Run: `uv run pytest tests/sdk/test_providers.py -v -x`
Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add src/sdk/providers/factory.py
git commit -m "feat(backend): check per-user stored API keys in provider factory"
```

---

### Task 4: Flutter — Settings/connectors Riverpod providers

**Files:**
- Create: `flutter_app/lib/features/settings/providers/settings_provider.dart`

- [ ] **Step 1: Write the settings provider**

```dart
import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../../../providers/agent_provider.dart';

class SettingsState {
  final String host;
  final String defaultModel;
  final Map<String, String> providerKeys; // provider_id -> api_key
  final Map<String, bool> providerKeyStatus; // provider_id -> has_key (from backend)
  final bool loading;
  final String? error;

  const SettingsState({
    this.host = '127.0.0.1:8080',
    this.defaultModel = 'deepseek:deepseek-v4-flash',
    this.providerKeys = const {},
    this.providerKeyStatus = const {},
    this.loading = false,
    this.error,
  });

  SettingsState copyWith({
    String? host,
    String? defaultModel,
    Map<String, String>? providerKeys,
    Map<String, bool>? providerKeyStatus,
    bool? loading,
    Object? error,
  }) {
    return SettingsState(
      host: host ?? this.host,
      defaultModel: defaultModel ?? this.defaultModel,
      providerKeys: providerKeys ?? this.providerKeys,
      providerKeyStatus: providerKeyStatus ?? this.providerKeyStatus,
      loading: loading ?? this.loading,
      error: error != null ? error.toString() : this.error,
    );
  }
}

class SettingsNotifier extends StateNotifier<SettingsState> {
  SettingsNotifier() : super(const SettingsState());

  String get _baseUrl => 'http://${state.host}';

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final host = prefs.getString('ea_host') ?? '127.0.0.1:8080';
    final model = prefs.getString('ea_model') ?? 'deepseek:deepseek-v4-flash';
    final keys = <String, String>{};
    for (final key in prefs.getKeys()) {
      if (key.startsWith('ea_key_')) {
        final pid = key.substring(7);
        final val = prefs.getString(key);
        if (val != null && val.isNotEmpty) keys[pid] = val;
      }
    }
    state = SettingsState(
      host: host,
      defaultModel: model,
      providerKeys: keys,
    );

    // Sync to global state
    ref.read(selectedModelProvider.notifier).state = model;
    ref.read(providerKeysProvider.notifier).state = Map.from(keys);

    // Fetch provider key status from backend
    try {
      final resp = await http.get(Uri.parse('http://$host/settings?user_id=default_user'));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final status = <String, bool>{};
        final providerStatus = data['provider_status'] as Map<String, dynamic>? ?? {};
        for (final entry in providerStatus.entries) {
          status[entry.key] = (entry.value as Map)['has_key'] == true;
        }
        state = state.copyWith(
          providerKeyStatus: status,
          defaultModel: data['default_model'] as String? ?? model,
        );
      }
    } catch (_) {
      // Offline — use local state
    }
  }

  Future<void> setDefaultModel(String model) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('ea_model', model);
    state = state.copyWith(defaultModel: model);
    ref.read(selectedModelProvider.notifier).state = model;
    ref.read(agentProvider.notifier).updateModel(model);
    // Sync to backend
    try {
      await http.patch(
        Uri.parse('$baseUrl/settings?user_id=default_user'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'default_model': model}),
      );
    } catch (_) {}
  }

  Future<void> setApiKey(String provider, String apiKey) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('ea_key_$provider', apiKey);
    state = state.copyWith(
      providerKeys: {...state.providerKeys, provider: apiKey},
      providerKeyStatus: {...state.providerKeyStatus, provider: apiKey.isNotEmpty},
    );
    ref.read(providerKeysProvider.notifier).state = Map.from(state.providerKeys);
    ref.read(agentProvider.notifier).updateProviderKeys(Map.from(state.providerKeys));
    // Sync to backend
    try {
      await http.post(
        Uri.parse('$baseUrl/settings/api-keys?user_id=default_user'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'provider': provider, 'api_key': apiKey}),
      );
    } catch (_) {}
  }

  Future<void> removeApiKey(String provider) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('ea_key_$provider');
    final keys = Map<String, String>.from(state.providerKeys)..remove(provider);
    final status = Map<String, bool>.from(state.providerKeyStatus)..remove(provider);
    state = state.copyWith(providerKeys: keys, providerKeyStatus: status);
    ref.read(providerKeysProvider.notifier).state = keys;
    // Sync to backend
    try {
      await http.delete(
        Uri.parse('$baseUrl/settings/api-keys/$provider?user_id=default_user'),
      );
    } catch (_) {}
  }

  void setHost(String host) {
    final prefs = SharedPreferences.getInstance();
    prefs.then((p) => p.setString('ea_host', host));
    state = state.copyWith(host: host);
  }
}

final settingsProvider = StateNotifierProvider<SettingsNotifier, SettingsState>((ref) {
  return SettingsNotifier();
});
```

- [ ] **Step 2: Verify it parses**

Run: `cd flutter_app && flutter analyze lib/features/settings/providers/settings_provider.dart`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add flutter_app/lib/features/settings/providers/settings_provider.dart
git commit -m "feat(flutter): add settings provider with API key and model management"
```

---

### Task 5: Flutter — Compact Settings Dialog

**Files:**
- Modify: `flutter_app/lib/features/settings/settings_screen.dart`

- [ ] **Step 1: Rewrite settings_screen.dart as compact dialog**

Replace contents with:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../providers/agent_provider.dart';
import 'providers/settings_provider.dart';
import '../../theme/app_theme.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  final VoidCallback? onManageProviders;
  const SettingsScreen({super.key, this.onManageProviders});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    ref.read(settingsProvider.notifier).load().then((_) {
      if (mounted) setState(() => _loading = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final settings = ref.watch(settingsProvider);

    return Scaffold(
      appBar: AppBar(
        title: Text('Ajustes', style: TextStyle(fontSize: 16)),
        leading: IconButton(
          icon: Icon(Symbols.close, size: 20),
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _sectionHeader('Server', tokens),
                _tile('URL', settings.host, Symbols.dns, readOnly: true, tokens: tokens),

                const SizedBox(height: 24),
                _sectionHeader('Default Model', tokens),
                _modelDropdown(settings, tokens),
                const SizedBox(height: 6),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: widget.onManageProviders ?? () {
                      Navigator.of(context).pop();
                    },
                    icon: Icon(Symbols.cable, size: 14),
                    label: Text('Manage providers →', style: TextStyle(fontSize: 12)),
                  ),
                ),

                const SizedBox(height: 24),
                _sectionHeader('About', tokens),
                _tile('Version', '0.1.0', Symbols.info, readOnly: true, tokens: tokens),
                _tile('Data Directory', '~/Assistant', Symbols.folder, readOnly: true, tokens: tokens),

                const SizedBox(height: 24),
                Center(
                  child: FilledButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: const Text('Done'),
                  ),
                ),
                const SizedBox(height: 24),
              ],
            ),
    );
  }

  Widget _sectionHeader(String title, var tokens) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(
        title,
        style: TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w600,
          color: tokens.colors.accent,
        ),
      ),
    );
  }

  Widget _tile(
    String title,
    String subtitle,
    IconData icon, {
    bool readOnly = false,
    required var tokens,
  }) {
    return ListTile(
      leading: Icon(icon, size: 20, color: tokens.colors.textSecondary),
      title: Text(title, style: TextStyle(fontSize: 13, color: tokens.colors.textPrimary)),
      subtitle: Text(subtitle, style: TextStyle(fontSize: 12, color: tokens.colors.textSecondary)),
      dense: true,
    );
  }

  Widget _modelDropdown(SettingsState settings, var tokens) {
    final modelStr = settings.defaultModel;
    final parts = modelStr.split(':');
    final currentProvider = parts.length > 1 ? parts[0] : '';
    final currentModelName = parts.length > 1 ? parts.sublist(1).join(':') : modelStr;

    // Build list from configured providers (have keys in SharedPreferences + backend)
    final configuredProviders = <String, List<String>>{};
    for (final entry in settings.providerKeys.entries) {
      if (entry.value.isNotEmpty) {
        configuredProviders[entry.key] = []; // models fetched below
      }
    }
    // Also add providers with keys from backend status
    for (final entry in settings.providerKeyStatus.entries) {
      if (entry.value && !configuredProviders.containsKey(entry.key)) {
        configuredProviders[entry.key] = [];
      }
    }

    // Dropdown shows "provider:model" items grouped by provider
    final items = <DropdownMenuItem<String>>[];
    for (final provider in configuredProviders.keys) {
      // We don't have model list here — user goes to Connectors to pick
      items.add(DropdownMenuItem(
        value: provider,
        enabled: false,
        child: Text(provider, style: TextStyle(fontWeight: FontWeight.w600, fontSize: 12)),
      ));
    }

    if (items.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(
          'No providers configured. Click "Manage providers" to add one.',
          style: TextStyle(fontSize: 12, color: tokens.colors.textTertiary),
        ),
      );
    }

    return DropdownButtonFormField<String>(
      value: modelStr,
      isExpanded: true,
      decoration: InputDecoration(
        isDense: true,
        border: OutlineInputBorder(),
        contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      ),
      items: [
        DropdownMenuItem(
          value: modelStr,
          child: Text(
            currentModelName,
            style: TextStyle(fontSize: 13),
          ),
        ),
      ],
      onChanged: (v) {
        if (v != null) {
          ref.read(settingsProvider.notifier).setDefaultModel(v);
        }
      },
    );
  }
}
```

Note: The model dropdown only shows the current selection. Full model picking happens in the Connectors LLM Providers tab. The dropdown is read-only here as a display.

- [ ] **Step 2: Verify it analyzes clean**

Run: `cd flutter_app && flutter analyze lib/features/settings/settings_screen.dart`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add flutter_app/lib/features/settings/settings_screen.dart
git commit -m "refactor(flutter): compact settings dialog with model dropdown and manage providers link"
```

---

### Task 6: Flutter — Connectors Modal Shell

**Files:**
- Create: `flutter_app/lib/features/connectors/connectors_modal.dart`

- [ ] **Step 1: Write the connectors modal with TabBar**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import 'widgets/llm_providers_tab.dart';
import 'widgets/services_tab.dart';

class ConnectorsModal extends ConsumerStatefulWidget {
  final int initialTab; // 0 = LLM Providers, 1 = Services
  const ConnectorsModal({super.key, this.initialTab = 0});

  @override
  ConsumerState<ConnectorsModal> createState() => _ConnectorsModalState();
}

class _ConnectorsModalState extends ConsumerState<ConnectorsModal>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this, initialIndex: widget.initialTab);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Conectors'),
        leading: IconButton(
          icon: const Icon(Symbols.close, size: 20),
          onPressed: () => Navigator.of(context).pop(),
        ),
        bottom: TabBar(
          controller: _tabController,
          tabs: const [
            Tab(icon: Icon(Symbols.psychiatry, size: 18), text: 'LLM Providers'),
            Tab(icon: Icon(Symbols.lan, size: 18), text: 'Services'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: const [
          LlmProvidersTab(),
          ServicesTab(),
        ],
      ),
    );
  }
}
```

- [ ] **Step 2: Verify it analyzes clean**

Run: `cd flutter_app && flutter analyze lib/features/connectors/connectors_modal.dart`
Expected: no errors (will have one for missing LlmProvidersTab/ServicesTab — that's expected until Task 7/8)

- [ ] **Step 3: Commit**

```bash
git add flutter_app/lib/features/connectors/connectors_modal.dart
git commit -m "feat(flutter): add connectors modal shell with tab bar"
```

---

### Task 7: Flutter — LLM Providers Tab

**Files:**
- Create: `flutter_app/lib/features/connectors/widgets/llm_providers_tab.dart`
- Create: `flutter_app/lib/features/connectors/widgets/provider_card.dart`

- [ ] **Step 1: Write ProviderCard widget**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import '../../../theme/app_theme.dart';
import '../../../providers/agent_provider.dart';
import '../../settings/providers/settings_provider.dart';

class ProviderCard extends ConsumerStatefulWidget {
  final String providerId;
  final String providerName;
  final bool hasKey;
  final List<String> models;
  final String? selectedModel;

  const ProviderCard({
    super.key,
    required this.providerId,
    required this.providerName,
    required this.hasKey,
    required this.models,
    this.selectedModel,
  });

  @override
  ConsumerState<ProviderCard> createState() => _ProviderCardState();
}

class _ProviderCardState extends ConsumerState<ProviderCard> {
  bool _expanded = false;
  late TextEditingController _keyCtrl;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _keyCtrl = TextEditingController();
  }

  @override
  void dispose() {
    _keyCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final settings = ref.read(settingsProvider.notifier);

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: Column(
        children: [
          ListTile(
            leading: Icon(
              widget.hasKey ? Symbols.radio_button_checked : Symbols.radio_button_unchecked,
              size: 18,
              color: widget.hasKey ? tokens.colors.accent : tokens.colors.textTertiary,
            ),
            title: Text(widget.providerName, style: const TextStyle(fontSize: 14)),
            subtitle: Text(
              widget.hasKey ? '🔑 Configured' : '⚠️ Needs API key',
              style: TextStyle(fontSize: 11, color: widget.hasKey ? Colors.green : Colors.orange),
            ),
            trailing: Icon(_expanded ? Symbols.expand_less : Symbols.expand_more, size: 18),
            onTap: () => setState(() => _expanded = !_expanded),
          ),
          if (_expanded) ...[
            if (!widget.hasKey)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                child: Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _keyCtrl,
                        obscureText: true,
                        decoration: InputDecoration(
                          hintText: 'API key for ${widget.providerName}',
                          isDense: true,
                          border: const OutlineInputBorder(),
                          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                          suffixIcon: _saving
                              ? const SizedBox(
                                  width: 16, height: 16,
                                  child: Padding(
                                    padding: EdgeInsets.all(10),
                                    child: CircularProgressIndicator(strokeWidth: 2),
                                  ),
                                )
                              : null,
                        ),
                        style: const TextStyle(fontSize: 13),
                      ),
                    ),
                    const SizedBox(width: 8),
                    FilledButton(
                      onPressed: () async {
                        setState(() => _saving = true);
                        await settings.setApiKey(widget.providerId, _keyCtrl.text);
                        setState(() => _saving = false);
                      },
                      style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 16)),
                      child: const Text('Save', style: TextStyle(fontSize: 12)),
                    ),
                  ],
                ),
              ),
            if (widget.hasKey) ...[
              const Divider(height: 1),
              if (widget.models.isEmpty)
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Text(
                    'No models found for this provider.',
                    style: TextStyle(fontSize: 12, color: tokens.colors.textTertiary),
                  ),
                )
              else
                ...widget.models.map((m) {
                  final modelValue = '${widget.providerId}:$m';
                  final isSelected = modelValue == widget.selectedModel;
                  return ListTile(
                    title: Text(m, style: const TextStyle(fontSize: 12)),
                    leading: Radio<String>(
                      value: modelValue,
                      groupValue: widget.selectedModel,
                      onChanged: (v) {
                        if (v != null) {
                          settings.setDefaultModel(v);
                        }
                      },
                    ),
                    dense: true,
                    selected: isSelected,
                  );
                }),
            ],
          ],
        ],
      ),
    );
  }
}
```

- [ ] **Step 2: Write LlmProvidersTab**

```dart
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../../theme/app_theme.dart';
import '../../../providers/agent_provider.dart';
import '../../settings/providers/settings_provider.dart';
import 'provider_card.dart';

class LlmProvidersTab extends ConsumerStatefulWidget {
  const LlmProvidersTab({super.key});

  @override
  ConsumerState<LlmProvidersTab> createState() => _LlmProvidersTabState();
}

class _LlmProvidersTabState extends ConsumerState<LlmProvidersTab> {
  bool _loading = true;
  List<Map<String, dynamic>> _providers = [];
  String _search = '';
  final _searchCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final host = ref.read(hostProvider);
      final resp = await http.get(Uri.parse('http://$host/models'));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final list = (data['providers'] as List? ?? [])
            .map((p) => p as Map<String, dynamic>)
            .toList();
        list.sort((a, b) => (a['name'] as String).compareTo(b['name'] as String));
        setState(() {
          _providers = list;
          _loading = false;
        });
        return;
      }
    } catch (_) {}
    setState(() => _loading = false);
  }

  List<Map<String, dynamic>> get _filtered {
    if (_search.isEmpty) return _providers;
    final q = _search.toLowerCase();
    return _providers.where((p) {
      if ((p['name'] as String).toLowerCase().contains(q)) return true;
      final models = p['models'] as List? ?? [];
      return models.any((m) => m.toString().toLowerCase().contains(q));
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final settings = ref.watch(settingsProvider);

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
          child: TextField(
            controller: _searchCtrl,
            decoration: InputDecoration(
              hintText: 'Search providers or models...',
              prefixIcon: Icon(Symbols.search, size: 18),
              isDense: true,
              border: OutlineInputBorder(),
              contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            ),
            onChanged: (v) => setState(() => _search = v),
          ),
        ),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : _filtered.isEmpty
                  ? Center(
                      child: Text(
                        _search.isNotEmpty ? 'No matches' : 'No providers loaded',
                        style: TextStyle(color: tokens.colors.textTertiary),
                      ),
                    )
                  : ListView.builder(
                      itemCount: _filtered.length,
                      itemBuilder: (_, i) {
                        final p = _filtered[i];
                        final pid = p['id'] as String;
                        final name = p['name'] as String? ?? pid;
                        final models = List<String>.from(p['models'] ?? []);
                        final hasKey = settings.providerKeys.containsKey(pid) && settings.providerKeys[pid]!.isNotEmpty
                            || settings.providerKeyStatus[pid] == true;
                        return ProviderCard(
                          providerId: pid,
                          providerName: name,
                          hasKey: hasKey,
                          models: models,
                          selectedModel: settings.defaultModel,
                        );
                      },
                    ),
        ),
      ],
    );
  }
}
```

- [ ] **Step 3: Verify both files analyze clean**

Run: `cd flutter_app && flutter analyze lib/features/connectors/widgets/provider_card.dart lib/features/connectors/widgets/llm_providers_tab.dart`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/features/connectors/widgets/provider_card.dart flutter_app/lib/features/connectors/widgets/llm_providers_tab.dart
git commit -m "feat(flutter): add LLM Providers tab with search and expandable provider cards"
```

---

### Task 8: Flutter — Services Tab

**Files:**
- Create: `flutter_app/lib/features/connectors/widgets/services_tab.dart`
- Create: `flutter_app/lib/features/connectors/widgets/connect_auth_form.dart`

- [ ] **Step 1: Write ConnectAuthForm — dynamic auth form**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import '../../../providers/agent_provider.dart';

class ConnectAuthForm extends ConsumerStatefulWidget {
  final Map<String, dynamic> spec;
  final VoidCallback onDone;

  const ConnectAuthForm({
    super.key,
    required this.spec,
    required this.onDone,
  });

  @override
  ConsumerState<ConnectAuthForm> createState() => _ConnectAuthFormState();
}

class _ConnectAuthFormState extends ConsumerState<ConnectAuthForm> {
  final _formKey = GlobalKey<FormState>();
  final Map<String, TextEditingController> _ctrls = {};
  bool _connecting = false;

  @override
  void dispose() {
    for (final c in _ctrls.values) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final spec = widget.spec;
    final fields = spec['required_fields'] as List? ?? [];
    final authType = spec['auth_type'] as String? ?? 'none';

    return Form(
      key: _formKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            'Connect ${spec['name'] ?? ''}',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 4),
          Text(
            spec['description'] ?? '',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 16),

          if (authType == 'oauth2') ...[
            Text('OAuth2 — click Sign In to authorize:', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 8),
            if (fields.isNotEmpty) ...[
              Text('Optional custom app settings:', style: Theme.of(context).textTheme.bodySmall),
              ...fields.map(_buildField),
            ],
          ] else if (authType == 'api_key')
            ...fields.map(_buildField)
          else if (authType == 'basic')
            ...fields.map(_buildField)
          else if (authType == 'none')
            Text('No credentials needed. Click Connect to enable.', style: Theme.of(context).textTheme.bodySmall),

          const SizedBox(height: 16),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('Cancel'),
              ),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: _connecting ? null : _connect,
                child: _connecting
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Connect'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildField(Map<String, dynamic> field) {
    final name = field['name'] as String;
    final label = field['label'] as String? ?? name;
    final type = field['type'] as String? ?? 'string';
    final ctrl = _ctrls.putIfAbsent(name, () => TextEditingController());
    final obscure = type == 'password' || name.toLowerCase().contains('secret');
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: TextFormField(
        controller: ctrl,
        obscureText: obscure,
        decoration: InputDecoration(
          labelText: label,
          isDense: true,
          border: const OutlineInputBorder(),
        ),
      ),
    );
  }

  Future<void> _connect() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _connecting = true);
    try {
      final body = <String, String>{};
      for (final entry in _ctrls.entries) {
        if (entry.value.text.isNotEmpty) {
          body[entry.key] = entry.value.text;
        }
      }
      final host = ref.read(hostProvider);
      final resp = await http.post(
        Uri.parse('http://$host/connectors/connect?service=${widget.spec['name']}&user_id=default_user'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(body),
      );
      if (resp.statusCode == 200) {
        widget.onDone();
        if (context.mounted) Navigator.of(context).pop();
      } else {
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Failed: ${resp.body}')),
          );
        }
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _connecting = false);
    }
  }
}
```

- [ ] **Step 2: Write ServicesTab**

```dart
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../../theme/app_theme.dart';
import '../../../providers/agent_provider.dart';
import 'connect_auth_form.dart';

class ServicesTab extends ConsumerStatefulWidget {
  const ServicesTab({super.key});

  @override
  ConsumerState<ServicesTab> createState() => _ServicesTabState();
}

class _ServicesTabState extends ConsumerState<ServicesTab> {
  bool _loading = true;
  List<Map<String, dynamic>> _allConnectors = [];
  Set<String> _connected = {};
  String _search = '';
  String? _categoryFilter;
  final _searchCtrl = TextEditingController();

  Set<String> get _categories {
    final cats = <String>{};
    for (final c in _allConnectors) {
      final cat = c['category'] as String? ?? 'Other';
      cats.add(cat);
    }
    return cats;
  }

  List<Map<String, dynamic>> get _filtered {
    var list = _allConnectors;
    if (_categoryFilter != null) {
      list = list.where((c) => (c['category'] as String? ?? 'Other') == _categoryFilter).toList();
    }
    if (_search.isNotEmpty) {
      final q = _search.toLowerCase();
      list = list.where((c) {
        final name = (c['name'] as String? ?? '').toLowerCase();
        final desc = (c['description'] as String? ?? '').toLowerCase();
        return name.contains(q) || desc.contains(q);
      }).toList();
    }
    // Pinned: connected first
    list.sort((a, b) {
      final aConn = _connected.contains(a['name']) ? 0 : 1;
      final bConn = _connected.contains(b['name']) ? 0 : 1;
      return aConn.compareTo(bConn);
    });
    return list;
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final host = ref.read(hostProvider);
      final resp = await http.get(Uri.parse('http://$host/connectors/catalog?user_id=default_user'));
      if (resp.statusCode == 200) {
        final list = (jsonDecode(resp.body) as List)
            .map((e) => e as Map<String, dynamic>)
            .toList();
        // Check connected status
        final connected = <String>{};
        for (final c in list) {
          if (c['connected'] == true) connected.add(c['name'] as String);
        }
        setState(() {
          _allConnectors = list;
          _connected = connected;
          _loading = false;
        });
        return;
      }
    } catch (_) {}
    setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: TextField(
            controller: _searchCtrl,
            decoration: InputDecoration(
              hintText: 'Search services...',
              prefixIcon: Icon(Symbols.search, size: 18),
              isDense: true,
              border: OutlineInputBorder(),
              contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            ),
            onChanged: (v) => setState(() => _search = v),
          ),
        ),
        SizedBox(
          height: 36,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            children: [
              _categoryChip(null, 'All', tokens),
              ..._categories.map((c) => _categoryChip(c, c, tokens)),
            ],
          ),
        ),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : _filtered.isEmpty
                  ? Center(child: Text('No services', style: TextStyle(color: tokens.colors.textTertiary)))
                  : ListView.builder(
                      itemCount: _filtered.length,
                      itemBuilder: (_, i) {
                        final c = _filtered[i];
                        final name = c['name'] as String;
                        final isConnected = _connected.contains(name);
                        return ListTile(
                          leading: Icon(
                            isConnected ? Symbols.check_circle : Symbols.lan,
                            size: 20,
                            color: isConnected ? Colors.green : tokens.colors.textSecondary,
                          ),
                          title: Text(name, style: const TextStyle(fontSize: 13)),
                          subtitle: Text(
                            c['description'] as String? ?? '',
                            style: TextStyle(fontSize: 11, color: tokens.colors.textTertiary),
                          ),
                          trailing: isConnected
                              ? TextButton(
                                  onPressed: () => _disconnect(name),
                                  child: Text('Disconnect', style: TextStyle(fontSize: 11, color: Colors.red)),
                                )
                              : TextButton(
                                  onPressed: () => _showConnectDialog(c),
                                  child: const Text('Connect', style: TextStyle(fontSize: 11)),
                                ),
                          dense: true,
                        );
                      },
                    ),
        ),
      ],
    );
  }

  Widget _categoryChip(String? category, String label, var tokens) {
    final isSelected = _categoryFilter == category;
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: ChoiceChip(
        label: Text(label, style: TextStyle(fontSize: 11)),
        selected: isSelected,
        onSelected: (_) => setState(() => _categoryFilter = category),
        visualDensity: VisualDensity.compact,
      ),
    );
  }

  void _showConnectDialog(Map<String, dynamic> spec) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        content: SizedBox(
          width: 400,
          child: ConnectAuthForm(
            spec: spec,
            onDone: () {
              setState(() => _connected.add(spec['name'] as String));
            },
          ),
        ),
      ),
    );
  }

  Future<void> _disconnect(String name) async {
    try {
      final host = ref.read(hostProvider);
      await http.delete(Uri.parse('http://$host/connectors/disconnect?service=$name&user_id=default_user'));
      setState(() => _connected.remove(name));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to disconnect: $e')),
        );
      }
    }
  }
}
```

- [ ] **Step 3: Verify both files analyze clean**

Run: `cd flutter_app && flutter analyze lib/features/connectors/widgets/services_tab.dart lib/features/connectors/widgets/connect_auth_form.dart`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/features/connectors/widgets/services_tab.dart flutter_app/lib/features/connectors/widgets/connect_auth_form.dart
git commit -m "feat(flutter): add Services tab with connector list and dynamic auth forms"
```

---

### Task 9: Flutter — Sidebar Integration + "Manage Providers" Bridge

**Files:**
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart`

- [ ] **Step 1: Add connectors to DesktopSidebarItem enum + wire up**

In `DesktopSidebarItem` enum, add a new entry before `settings`:

```dart
enum DesktopSidebarItem {
  email(
    icon: Symbols.mail,
    activeIcon: Symbols.mail,
    label: 'Email',
    path: '/email',
  ),
  workspace(
    icon: Symbols.folder,
    activeIcon: Symbols.folder,
    label: 'Workspace',
    path: '/workspace',
  ),
  connectors(
    icon: Symbols.cable,
    activeIcon: Symbols.cable,
    label: 'Conectors',
    path: '/connectors',
  ),
  settings(
    icon: Symbols.settings,
    activeIcon: Symbols.settings,
    label: 'Ajustes',
    path: '/settings',
  );
```

Add import at top:
```dart
import '../../features/connectors/connectors_modal.dart';
```

Add method to open connectors modal:
```dart
void _showConnectors(BuildContext context, {int tab = 0}) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (_) => ConnectorsModal(initialTab: tab),
  );
}
```

In the sidebar bottom Row, replace the single settings icon with two items:

```dart
Padding(
  padding: EdgeInsets.symmetric(
    horizontal: tokens.spacing.md,
    vertical: tokens.spacing.xs,
  ),
  child: Row(
    children: [
      Expanded(
        child: _SidebarItem(
          item: DesktopSidebarItem.connectors,
          selected: false,
          onTap: () => _showConnectors(context),
        ),
      ),
      Expanded(
        child: _SidebarItem(
          item: DesktopSidebarItem.settings,
          selected: false,
          onTap: () => _showSettings(context),
        ),
      ),
      IconButton(
        icon: Icon(
          tokens.isDark ? Symbols.light_mode : Symbols.dark_mode,
          size: 18,
          color: tokens.colors.textSecondary,
        ),
        onPressed: () =>
            ref.read(themeModeProvider.notifier).toggle(),
        tooltip: tokens.isDark
            ? 'Switch to light mode'
            : 'Switch to dark mode',
      ),
    ],
  ),
),
```

Update `_showSettings` to pass the callback:

```dart
void _showSettings(BuildContext context) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (_) => SettingsScreen(
      onManageProviders: () {
        Navigator.of(context).pop(); // close settings
        // Small delay for bottom sheet close animation
        Future.delayed(const Duration(milliseconds: 300), () {
          _showConnectors(context, tab: 0);
        });
      },
    ),
  );
}
```

- [ ] **Step 2: Verify desktop_layout.dart analyzes clean**

Run: `cd flutter_app && flutter analyze lib/core/layout/desktop_layout.dart`
Expected: no errors

- [ ] **Step 3: Run full flutter analyze**

Run: `cd flutter_app && flutter analyze`
Expected: no errors (or only pre-existing ones)

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "feat(flutter): add connectors sidebar icon and manage providers bridge"
```

---

## Self-Review

**1. Spec coverage:**
- Settings dialog: compact, server URL, default model dropdown, about → **Task 5**
- Connectors modal with two tabs → **Task 6**
- LLM Providers tab with search + tree view + API key input + model selection → **Task 7**
- Services tab with search + category chips + connect/disconnect flow → **Task 8**
- Dynamic auth form (OAuth2, API key, Basic, None) → **Task 8** (ConnectAuthForm)
- Sidebar connectors icon + settings icon → **Task 9**
- "Manage providers" bridge → **Task 9**
- Backend API for key storage and default model → **Tasks 1-3**
- Provider factory checks stored keys → **Task 3**

**2. Placeholder scan:** All tasks contain concrete code. No TBDs, TODOs, or "implement later" patterns.

**3. Type consistency:** `SettingsNotifier` methods used consistently: `setApiKey()`, `setDefaultModel()`, `load()`. `SettingsState` fields: `host`, `defaultModel`, `providerKeys`, `providerKeyStatus`. `ConnectorsModal` takes `initialTab` parameter. `ProviderCard` props match usage in `LlmProvidersTab`.
