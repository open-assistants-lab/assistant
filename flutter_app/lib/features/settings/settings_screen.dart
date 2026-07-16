import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../models/provider_model.dart';
import '../../providers/agent_provider.dart';
import '../../theme/app_theme.dart';
import '../connectors/widgets/provider_card.dart';
import '../workspace/learn_checklist_provider.dart';
import 'providers/settings_provider.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  bool _loadingSettings = true;
  bool _loadingProviders = true;
  List<Map<String, dynamic>> _providers = [];
  String _search = '';
  final _searchCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    ref.read(settingsProvider.notifier).load().then((_) {
      if (mounted) setState(() => _loadingSettings = false);
    });
    _loadProviders();
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  List<ProviderModel> _parseModels(List models) {
    return models.map((m) {
      if (m is Map) {
        return (id: m['id'] as String? ?? '', name: m['name'] as String? ?? '');
      }
      return (id: m.toString(), name: m.toString());
    }).toList();
  }

  Future<void> _loadProviders() async {
    try {
      final host = ref.read(hostProvider);
      final resp = await http.get(Uri.parse('http://$host/models'));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final list = (data['providers'] as List? ?? [])
            .map((p) {
              final m = p as Map<String, dynamic>;
              m['models'] = _parseModels(m['models'] as List? ?? []);
              return m;
            })
            .toList();
        setState(() {
          _providers = list;
          _loadingProviders = false;
        });
        return;
      }
    } catch (_) {}
    setState(() => _loadingProviders = false);
  }

  List<Map<String, dynamic>> get _sortedProviders {
    final settings = ref.read(settingsProvider);
    var list = _providers;
    if (_search.isNotEmpty) {
      final q = _search.toLowerCase();
      list = list.where((p) {
        if ((p['name'] as String).toLowerCase().contains(q)) return true;
        final models = p['models'] as List? ?? [];
        return models.any((m) {
          final model = m as ProviderModel;
          return model.name.toLowerCase().contains(q) || model.id.toLowerCase().contains(q);
        });
      }).toList();
    }
    list.sort((a, b) {
      final aConnected = _isConnected(a, settings);
      final bConnected = _isConnected(b, settings);
      if (aConnected && !bConnected) return -1;
      if (!aConnected && bConnected) return 1;
      return (a['name'] as String).compareTo(b['name'] as String);
    });
    return list;
  }

  bool _isConnected(Map<String, dynamic> p, SettingsState settings) {
    final pid = p['id'] as String;
    return (settings.providerKeys[pid]?.isNotEmpty ?? false) ||
        settings.providerKeyStatus[pid] == true;
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final settings = ref.watch(settingsProvider);

    return Container(
      color: tokens.colors.bgCanvas,
      child: Column(
        children: [
          Padding(
            padding: EdgeInsets.fromLTRB(
                tokens.spacing.md,
                tokens.spacing.lg,
                tokens.spacing.md,
                tokens.spacing.md,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Symbols.settings,
                        size: 18, color: tokens.colors.accent),
                    const SizedBox(width: 8),
                    Text('Settings',
                        style: tokens.typography.textTheme.titleLarge
                            ?.copyWith(color: tokens.colors.textPrimary)),
                    const Spacer(),
                  ],
                ),
              ],
            ),
          ),
          Expanded(
            child: _loadingSettings
                ? const Center(child: CircularProgressIndicator())
                : ListView(
            padding: EdgeInsets.fromLTRB(
                tokens.spacing.md,
                tokens.spacing.lg,
                tokens.spacing.md,
                tokens.spacing.md,
            ),
                    children: [
                      _sectionHeader('Server', tokens),
                      _tile('URL', settings.host, Symbols.dns, tokens: tokens),

                      const SizedBox(height: 24),
                      _sectionHeader('Default Model', tokens),
                      _modelDropdown(settings, tokens),
                      const SizedBox(height: 6),

                      const SizedBox(height: 24),
                      _sectionHeader('LLM Providers', tokens),
                      TextField(
                        controller: _searchCtrl,
                        decoration: InputDecoration(
                          hintText: 'Search providers or models...',
                          prefixIcon: const Icon(Symbols.search, size: 18),
                          isDense: true,
                          border: const OutlineInputBorder(),
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 12,
                            vertical: 10,
                          ),
                        ),
                        onChanged: (v) => setState(() => _search = v),
                      ),
                      const SizedBox(height: 8),
                      if (_loadingProviders)
                        const Padding(
                          padding: EdgeInsets.all(24),
                          child: Center(child: CircularProgressIndicator()),
                        )
                      else if (_sortedProviders.isEmpty)
                        Padding(
                          padding: const EdgeInsets.all(16),
                          child: Text(
                            _search.isNotEmpty ? 'No matches' : 'No providers loaded',
                            style: TextStyle(color: tokens.colors.textTertiary, fontSize: 13),
                          ),
                        )
                      else
                        ..._sortedProviders.map((p) {
                          final pid = p['id'] as String;
                          final name = p['name'] as String? ?? pid;
                          final models = List<ProviderModel>.from(p['models'] ?? []);
                          final hasKey =
                              (settings.providerKeys.containsKey(pid) &&
                                      settings.providerKeys[pid]!.isNotEmpty) ||
                                  settings.providerKeyStatus[pid] == true;
                          return ProviderCard(
                            providerId: pid,
                            providerName: name,
                            hasKey: hasKey,
                            models: models,
                            selectedModel: settings.defaultModel,
                          );
                        }),

                      const SizedBox(height: 24),
                      _sectionHeader('Tutorial', tokens),
                      _tile(
                        'Reset Tutorial Cards',
                        'Show the learn checklist again',
                        Symbols.restart_alt,
                        tokens: tokens,
                        onTap: () {
                          ref.read(learnChecklistProvider.notifier).reset();
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text('Tutorial cards reset — they will reappear on reload.'),
                              duration: Duration(seconds: 2),
                            ),
                          );
                        },
                      ),
                      const SizedBox(height: 24),
                      _sectionHeader('About', tokens),
                      _tile('Version', '0.1.0', Symbols.info, tokens: tokens),
                      _tile(
                        'Data Directory',
                        '~/Assistant',
                        Symbols.folder,
                        tokens: tokens,
                      ),
                      _tile(
                        'Report Issue',
                        'Open a pre-filled GitHub issue',
                        Symbols.bug_report,
                        tokens: tokens,
                        onTap: _reportIssue,
                      ),
                      const SizedBox(height: 24),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  Future<void> _reportIssue() async {
    final info = await PackageInfo.fromPlatform();
    final version = '${info.version}+${info.buildNumber}';
    final platform = 'macOS';
    final backendStatus = ref.read(agentProvider).status;

    final body = '''
## Environment

- **App Version:** $version
- **Platform:** $platform
- **Backend Status:** $backendStatus

## Description

<!-- Describe the issue here -->

## Steps to Reproduce

1. 
2. 
3. 

## Expected Behavior



## Actual Behavior



## Additional Context

''';

    final uri = Uri.parse(
      'https://github.com/open-assistants-lab/assistant/issues/new'
      '?labels=bug&template=bug_report.md'
      '&title=${Uri.encodeComponent('[BUG] ')}'
      '&body=${Uri.encodeComponent(body)}',
    );
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
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
    required var tokens,
    VoidCallback? onTap,
  }) {
    return ListTile(
      leading: Icon(icon, size: 20, color: tokens.colors.textSecondary),
      title: Text(
        title,
        style: TextStyle(fontSize: 13, color: tokens.colors.textPrimary),
      ),
      subtitle: Text(
        subtitle,
        style: TextStyle(fontSize: 12, color: tokens.colors.textSecondary),
      ),
      trailing: onTap != null
          ? Icon(Symbols.open_in_new, size: 14, color: tokens.colors.accent)
          : null,
      dense: true,
      onTap: onTap,
    );
  }

  Widget _modelDropdown(SettingsState settings, var tokens) {
    final connectedProviders = _sortedProviders
        .where((p) => _isConnected(p, settings))
        .toList();

    if (connectedProviders.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(
          'No providers configured. Add an API key below to get started.',
          style: TextStyle(fontSize: 12, color: tokens.colors.textTertiary),
        ),
      );
    }

    final modelStr = settings.defaultModel;
    final items = <DropdownMenuItem<String>>[];

    for (final p in connectedProviders) {
      final pid = p['id'] as String;
      final name = p['name'] as String? ?? pid;
      final models = List<ProviderModel>.from(p['models'] ?? []);

      items.add(DropdownMenuItem<String>(
        value: '__header__$pid',
        enabled: false,
        child: Text(
          name,
          style: TextStyle(
            fontWeight: FontWeight.w600,
            fontSize: 12,
            color: tokens.colors.textSecondary,
          ),
        ),
      ));

      for (final m in models) {
        final modelValue = '$pid:${m.id}';
        items.add(DropdownMenuItem<String>(
          value: modelValue,
          child: Row(
            children: [
              Icon(
                modelValue == modelStr
                    ? Symbols.radio_button_checked
                    : Symbols.radio_button_unchecked,
                size: 14,
                color: modelValue == modelStr
                    ? tokens.colors.accent
                    : tokens.colors.textTertiary,
              ),
              const SizedBox(width: 8),
              Text(m.name, style: const TextStyle(fontSize: 13)),
            ],
          ),
        ));
      }
    }

    return DropdownButtonFormField<String>(
      initialValue: modelStr,
      isExpanded: true,
      decoration: const InputDecoration(
        isDense: true,
        border: OutlineInputBorder(),
        contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      ),
      items: items,
      onChanged: (v) {
        if (v != null && !v.startsWith('__header__')) {
          ref.read(settingsProvider.notifier).setDefaultModel(v);
        }
      },
    );
  }
}
