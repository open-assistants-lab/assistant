import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:material_symbols_icons/symbols.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:http/http.dart' as http;
import '../../../providers/agent_provider.dart';
import '../../../features/settings/settings_screen.dart';

class ProviderInfo {
  final String id;
  final String name;
  final List<String> models;
  const ProviderInfo({
    required this.id,
    required this.name,
    required this.models,
  });
}

class ModelSwitcher extends ConsumerStatefulWidget {
  const ModelSwitcher({super.key});

  @override
  ConsumerState<ModelSwitcher> createState() => _ModelSwitcherState();
}

class _ModelSwitcherState extends ConsumerState<ModelSwitcher> {
  List<ProviderInfo> _providers = [];

  @override
  void initState() {
    super.initState();
    _loadProviders();
  }

  Future<void> _hydrateSavedModelSettings() async {
    final prefs = await SharedPreferences.getInstance();
    final keys = <String, String>{};
    for (final key in prefs.getKeys()) {
      if (!key.startsWith('ea_key_')) continue;
      final providerId = key.substring(7);
      final value = prefs.getString(key);
      if (value != null && value.isNotEmpty) keys[providerId] = value;
    }
    final savedModel = prefs.getString('ea_model');
    if (!mounted) return;
    if (keys.isNotEmpty) {
      ref.read(providerKeysProvider.notifier).state = keys;
      ref.read(agentProvider.notifier).updateProviderKeys(keys);
    }
    if (savedModel != null && savedModel.isNotEmpty) {
      ref.read(selectedModelProvider.notifier).state = savedModel;
      ref.read(agentProvider.notifier).updateModel(savedModel);
    }
  }

  Future<void> _loadProviders() async {
    try {
      await _hydrateSavedModelSettings();
      final host = ref.read(hostProvider);
      final url = Uri.parse('http://$host/models');
      final resp = await http.get(url);
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final list = <ProviderInfo>[];
        for (final p in (data['providers'] ?? []) as List) {
          final models = List<String>.from(p['models'] ?? []);
          if (models.isNotEmpty) {
            list.add(
              ProviderInfo(
                id: p['id']?.toString() ?? '',
                name: p['name']?.toString() ?? p['id']?.toString() ?? '',
                models: models,
              ),
            );
          }
        }
        if (mounted) setState(() => _providers = list);
      }
    } catch (_) {}
  }

  Future<Map<String, String>> _loadProviderKeys() async {
    final prefs = await SharedPreferences.getInstance();
    final keys = <String, String>{};
    for (final p in _providers) {
      final k = prefs.getString('ea_key_${p.id}');
      if (k != null && k.isNotEmpty) keys[p.id] = k;
    }
    return keys;
  }

  List<ProviderInfo> get _authorizedProviders {
    final keys = ref.read(providerKeysProvider);
    if (keys.isEmpty) return [];
    final result = <ProviderInfo>[];
    for (final p in _providers) {
      if (keys.containsKey(p.id) && keys[p.id]!.isNotEmpty) {
        result.add(p);
      }
    }
    return result;
  }

  String _formatModelLabel(String selected) {
    final parts = selected.split(':');
    if (parts.length < 2) return selected;
    final pid = parts[0];
    final p = _providers.where((p) => p.id == pid).firstOrNull;
    final provider = p?.name ?? pid;
    final model = parts.sublist(1).join(':');
    return '$provider / $model';
  }

  String _selectedModelKey() {
    final effective = ref.read(selectedModelProvider);
    return _formatModelLabel(effective);
  }

  void _showPicker() async {
    final authorized = _authorizedProviders;
    if (authorized.isEmpty) {
      showModalBottomSheet(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => const SettingsScreen(),
      );
      return;
    }

    final selectedModel = ref.read(selectedModelProvider);
    final result = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _ModelPickerSheet(
        providers: authorized,
        selectedModel: selectedModel,
        hasOverride: false,
      ),
    );

    if (result == null || result == selectedModel) return;

    final prefs = await SharedPreferences.getInstance();
    if (result != '__default__') {
      await prefs.setString('ea_last_model', result);
    }
    ref.read(selectedModelProvider.notifier).state = result;
    ref.read(agentProvider.notifier).updateModel(result);

    final keys = await _loadProviderKeys();
    if (keys.isNotEmpty) {
      ref.read(providerKeysProvider.notifier).state = keys;
      ref.read(agentProvider.notifier).updateProviderKeys(keys);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_providers.isEmpty) return const SizedBox.shrink();

    final keys = ref.watch(providerKeysProvider);
    final hasKeys = keys.isNotEmpty;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return GestureDetector(
      onTap: _showPicker,
      child: Container(
        height: 28,
        padding: const EdgeInsets.symmetric(horizontal: 8),
        decoration: BoxDecoration(
          color: isDark ? const Color(0x20FFFFFF) : const Color(0x0D000000),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: hasKeys
                ? (isDark ? const Color(0x30FFFFFF) : const Color(0x1A000000))
                : (isDark ? const Color(0x18FFFFFF) : const Color(0x0F000000)),
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              hasKeys ? _selectedModelKey() : '+ Add model',
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w500,
                color: isDark
                    ? const Color(0xB3FFFFFF)
                    : const Color(0xB3000000),
              ),
            ),
            const SizedBox(width: 2),
            Icon(
              hasKeys ? Symbols.expand_more : Symbols.add_circle,
              size: 14,
              color: isDark ? const Color(0x80FFFFFF) : const Color(0x80000000),
            ),
          ],
        ),
      ),
    );
  }
}

class _ModelPickerSheet extends StatelessWidget {
  final List<ProviderInfo> providers;
  final String selectedModel;
  final bool hasOverride;

  const _ModelPickerSheet({
    required this.providers,
    required this.selectedModel,
    required this.hasOverride,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Container(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.65,
      ),
      decoration: BoxDecoration(
        color: theme.scaffoldBackgroundColor,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(height: 8),
          Container(
            width: 32,
            height: 4,
            decoration: BoxDecoration(
              color: Colors.grey[400],
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                const Text(
                  'Select Model',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                ),
                const Spacer(),
                IconButton(
                  icon: const Icon(Symbols.settings, size: 20),
                  onPressed: () {
                    Navigator.pop(context);
                    showModalBottomSheet(
                      context: context,
                      isScrollControlled: true,
                      useSafeArea: true,
                      builder: (_) => const SettingsScreen(),
                    );
                  },
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          Flexible(
            child: ListView(
              shrinkWrap: true,
              padding: const EdgeInsets.only(bottom: 32),
              children: [
                if (hasOverride)
                  _ModelRow(
                    value: '__default__',
                    label: 'Use default model',
                    isSelected: false,
                    onTap: () => Navigator.pop(context, '__default__'),
                  ),
                for (final p in providers) ...[
                  Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 6,
                    ),
                    child: Text(
                      p.name,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.5,
                        ),
                      ),
                    ),
                  ),
                  for (final m in p.models)
                    _ModelRow(
                      value: '${p.id}:$m',
                      label: m,
                      isSelected: '${p.id}:$m' == selectedModel,
                      onTap: () => Navigator.pop(context, '${p.id}:$m'),
                    ),
                  const SizedBox(height: 8),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ModelRow extends StatelessWidget {
  final String value;
  final String label;
  final bool isSelected;
  final VoidCallback onTap;

  const _ModelRow({
    required this.value,
    required this.label,
    required this.isSelected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return InkWell(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
        decoration: BoxDecoration(
          color: isSelected
              ? (theme.colorScheme.primary.withValues(alpha: 0.12))
              : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: [
            Expanded(
              child: Text(
                label,
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
                ),
              ),
            ),
            if (isSelected)
              Icon(Symbols.check, size: 18, color: theme.colorScheme.primary),
          ],
        ),
      ),
    );
  }
}
