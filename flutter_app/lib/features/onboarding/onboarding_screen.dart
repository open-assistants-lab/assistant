import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../core/error_messages.dart';
import '../../providers/agent_provider.dart';
import '../../theme/app_theme.dart';
import '../settings/providers/settings_provider.dart';
import 'onboarding_provider.dart';

class OnboardingScreen extends ConsumerStatefulWidget {
  final List<Map<String, dynamic>>? initialProviders;

  const OnboardingScreen({super.key, this.initialProviders});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  int _step = 0;
  bool _loading = true;
  bool _waitingForBackend = false;
  bool _backendTimedOut = false;
  bool _keyVisible = false;
  List<Map<String, dynamic>> _providers = [];
  final _providerFilterCtrl = TextEditingController();
  String _providerFilter = '';
  String? _selectedProvider;
  final _keyCtrl = TextEditingController();
  bool _testing = false;
  bool? _testResult;
  String? _testError;
  Timer? _healthTimer;
  Timer? _timeoutTimer;

  @override
  void initState() {
    super.initState();
    final initialProviders = widget.initialProviders;
    if (initialProviders != null) {
      _providers = List<Map<String, dynamic>>.from(initialProviders);
      _loading = false;
      return;
    }
    _loadProviders().then((loaded) {
      if (!loaded && _providers.isEmpty) {
        _startHealthPoll();
      }
    });
  }

  @override
  void dispose() {
    _keyCtrl.dispose();
    _providerFilterCtrl.dispose();
    _healthTimer?.cancel();
    _timeoutTimer?.cancel();
    super.dispose();
  }

  void _startHealthPoll() {
    setState(() => _waitingForBackend = true);
    _healthTimer?.cancel();
    _healthTimer = Timer.periodic(const Duration(seconds: 1), (_) async {
      try {
        final host = ref.read(hostProvider);
        final resp = await http.get(Uri.parse('http://$host/health'));
        if (resp.statusCode == 200) {
          _healthTimer?.cancel();
          _timeoutTimer?.cancel();
          _loadProviders();
        }
      } catch (_) {}
    });
    _timeoutTimer = Timer(const Duration(seconds: 10), () {
      if (mounted) setState(() => _backendTimedOut = true);
    });
  }

  Future<bool> _loadProviders() async {
    try {
      final host = ref.read(hostProvider);
      final resp = await http.get(Uri.parse('http://$host/models'));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final list = (data['providers'] as List? ?? [])
            .map((p) => p as Map<String, dynamic>)
            .toList();
        list.sort(
          (a, b) => (a['name'] as String).compareTo(b['name'] as String),
        );
        if (!mounted) return true;
        setState(() {
          _providers = list;
          _loading = false;
          _waitingForBackend = false;
        });
        return true;
      }
    } catch (_) {}
    if (!mounted) return false;
    setState(() => _loading = false);
    return false;
  }

  Future<void> _testKey() async {
    final key = _keyCtrl.text.trim();
    if (key.isEmpty || _selectedProvider == null) return;

    setState(() {
      _testing = true;
      _testResult = null;
      _testError = null;
    });

    try {
      final host = ref.read(hostProvider);
      final resp = await http.post(
        Uri.parse('http://$host/settings/test-key'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'provider': _selectedProvider, 'api_key': key}),
      );
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _testing = false;
        _testResult = data['valid'] == true;
        _testError = data['error'] as String?;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _testing = false;
        _testResult = false;
        _testError = humanReadableError('Could not reach backend: $e');
      });
    }
  }

  Future<void> _finish() async {
    if (_selectedProvider != null && _keyCtrl.text.trim().isNotEmpty) {
      final settings = ref.read(settingsProvider.notifier);
      await settings.setApiKey(_selectedProvider!, _keyCtrl.text.trim());
    }
    await ref.read(onboardingCompleteProvider.notifier).complete();
  }

  String? _providerName(String? id) {
    if (id == null) return null;
    for (final p in _providers) {
      if (p['id'] == id) return p['name'] as String? ?? id;
    }
    return id;
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;

    return Scaffold(
      backgroundColor: tokens.colors.bgCanvas,
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 48),
            child: _loading
                ? Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (!_backendTimedOut)
                          const CircularProgressIndicator(),
                        if (_waitingForBackend && !_backendTimedOut) ...[
                          const SizedBox(height: 16),
                          Text(
                            'Starting up…',
                            style: tokens.typography.textTheme.bodyMedium
                                ?.copyWith(color: tokens.colors.textSecondary),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            'The backend is starting — this should only take a moment.',
                            textAlign: TextAlign.center,
                            style: tokens.typography.textTheme.bodySmall
                                ?.copyWith(color: tokens.colors.textTertiary),
                          ),
                        ],
                        if (_backendTimedOut) ...[
                          const SizedBox(height: 16),
                          Icon(
                            Symbols.cloud_off,
                            size: 40,
                            color: tokens.colors.warning,
                          ),
                          const SizedBox(height: 12),
                          Text(
                            'Can\'t reach the backend server',
                            style: tokens.typography.textTheme.titleSmall
                                ?.copyWith(
                                  color: tokens.colors.textPrimary,
                                  fontWeight: FontWeight.w600,
                                ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'This app needs a companion server to run.\n'
                            'Open a terminal and run:\n\n'
                            '   uv run ea http\n\n'
                            'Then tap Retry below.',
                            textAlign: TextAlign.center,
                            style: tokens.typography.textTheme.bodySmall
                                ?.copyWith(
                                  color: tokens.colors.textSecondary,
                                  height: 1.5,
                                ),
                          ),
                          const SizedBox(height: 20),
                          FilledButton.icon(
                            icon: const Icon(Symbols.refresh, size: 18),
                            label: const Text('Retry'),
                            onPressed: () {
                              setState(() {
                                _loading = true;
                                _backendTimedOut = false;
                                _waitingForBackend = false;
                              });
                              _loadProviders().then((loaded) {
                                if (!loaded && _providers.isEmpty) {
                                  _startHealthPoll();
                                }
                              });
                            },
                          ),
                        ],
                      ],
                    ),
                  )
                : _buildContent(tokens),
          ),
        ),
      ),
    );
  }

  List<Map<String, dynamic>> get _filteredProviders {
    final q = _providerFilter.toLowerCase().trim();
    if (q.isEmpty) return _providers;
    return _providers.where((p) {
      final name = (p['name'] as String? ?? '').toLowerCase();
      final id = (p['id'] as String? ?? '').toLowerCase();
      return name.contains(q) || id.contains(q);
    }).toList();
  }

  Widget _buildContent(EaTokens tokens) {
    return switch (_step) {
      0 => _buildWelcome(tokens),
      1 => _buildApiKeyStep(tokens),
      _ => _buildDoneStep(tokens),
    };
  }

  Widget _buildWelcome(EaTokens tokens) {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Symbols.assistant, size: 48, color: tokens.colors.accent),
        const SizedBox(height: 24),
        Text(
          'Welcome to\nAssistant',
          style: tokens.typography.textTheme.headlineMedium?.copyWith(
            color: tokens.colors.textPrimary,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 12),
        Text(
          'Your AI-powered assistant for email, tasks, research, and more. '
          'Let\'s get you set up in under a minute.',
          style: tokens.typography.textTheme.bodyMedium?.copyWith(
            color: tokens.colors.textSecondary,
          ),
        ),
        const SizedBox(height: 32),
        Text(
          'Choose your AI provider',
          style: tokens.typography.textTheme.titleSmall?.copyWith(
            color: tokens.colors.textPrimary,
          ),
        ),
        const SizedBox(height: 8),
        SizedBox(
          height: 36,
          child: TextField(
            controller: _providerFilterCtrl,
            onChanged: (v) => setState(() => _providerFilter = v),
            style: const TextStyle(fontSize: 13),
            decoration: InputDecoration(
              hintText: 'Search providers…',
              prefixIcon: Icon(Symbols.search, size: 16),
              suffixIcon: _providerFilter.isNotEmpty
                  ? IconButton(
                      icon: Icon(Symbols.close, size: 16),
                      onPressed: () {
                        _providerFilterCtrl.clear();
                        setState(() => _providerFilter = '');
                      },
                    )
                  : null,
              contentPadding: const EdgeInsets.symmetric(
                vertical: 0,
                horizontal: 12,
              ),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
              ),
              isDense: true,
            ),
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: _providers.isEmpty
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        'No providers found',
                        style: tokens.typography.textTheme.titleSmall?.copyWith(
                          color: tokens.colors.textTertiary,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        'The backend server doesn\'t have any AI providers configured.\n'
                        'Add your API keys in Settings or check the server logs.',
                        textAlign: TextAlign.center,
                        style: tokens.typography.textTheme.bodySmall?.copyWith(
                          color: tokens.colors.textTertiary,
                          height: 1.5,
                        ),
                      ),
                    ],
                  ),
                )
              : ListView.builder(
                  itemCount: _filteredProviders.length,
                  itemBuilder: (_, i) {
                    final p = _filteredProviders[i];
                    final pid = p['id'] as String;
                    final name = p['name'] as String? ?? pid;
                    final selected = _selectedProvider == pid;
                    return Card(
                      margin: const EdgeInsets.symmetric(vertical: 3),
                      color: selected
                          ? tokens.colors.accent.withAlpha(25)
                          : null,
                      child: ListTile(
                        leading: Icon(
                          selected
                              ? Symbols.radio_button_checked
                              : Symbols.radio_button_unchecked,
                          size: 20,
                          color: selected
                              ? tokens.colors.accent
                              : tokens.colors.textTertiary,
                        ),
                        title: Text(name, style: const TextStyle(fontSize: 14)),
                        subtitle: Text(
                          '${(p['models'] as List?)?.length ?? 0} models',
                          style: TextStyle(
                            fontSize: 11,
                            color: tokens.colors.textTertiary,
                          ),
                        ),
                        onTap: () => setState(() => _selectedProvider = pid),
                        dense: true,
                      ),
                    );
                  },
                ),
        ),
        const SizedBox(height: 16),
        SizedBox(
          width: double.infinity,
          child: FilledButton(
            onPressed: _selectedProvider == null
                ? null
                : () => setState(() => _step = 1),
            child: const Text('Next'),
          ),
        ),
      ],
    );
  }

  Widget _buildApiKeyStep(EaTokens tokens) {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            IconButton(
              icon: const Icon(Symbols.arrow_back, size: 20),
              onPressed: () => setState(() => _step = 0),
              visualDensity: VisualDensity.compact,
            ),
            const SizedBox(width: 4),
            Expanded(
              child: Text(
                'Enter API Key',
                style: tokens.typography.textTheme.titleMedium?.copyWith(
                  color: tokens.colors.textPrimary,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          'Your key is stored locally and never shared.',
          style: tokens.typography.textTheme.bodySmall?.copyWith(
            color: tokens.colors.textSecondary,
          ),
        ),
        const SizedBox(height: 24),
        Text(
          'Provider: ${_providerName(_selectedProvider)}',
          style: tokens.typography.textTheme.titleSmall?.copyWith(
            color: tokens.colors.textPrimary,
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _keyCtrl,
          obscureText: !_keyVisible,
          decoration: InputDecoration(
            hintText: 'Paste your API key here',
            border: const OutlineInputBorder(),
            isDense: true,
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 12,
              vertical: 12,
            ),
            suffixIcon: _testing
                ? const Padding(
                    padding: EdgeInsets.all(10),
                    child: SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                : IconButton(
                    icon: Icon(
                      _keyVisible ? Symbols.visibility_off : Symbols.visibility,
                      size: 18,
                    ),
                    onPressed: () => setState(() => _keyVisible = !_keyVisible),
                  ),
          ),
          style: const TextStyle(fontSize: 14),
          onChanged: (_) {
            setState(() {
              if (_testResult != null) {
                _testResult = null;
                _testError = null;
              }
            });
          },
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            OutlinedButton.icon(
              onPressed: _testing || _keyCtrl.text.trim().isEmpty
                  ? null
                  : _testKey,
              icon: _testing
                  ? const SizedBox(
                      width: 12,
                      height: 12,
                      child: CircularProgressIndicator(strokeWidth: 1.5),
                    )
                  : const Icon(Symbols.network_check, size: 14),
              label: const Text('Test', style: TextStyle(fontSize: 12)),
              style: OutlinedButton.styleFrom(
                visualDensity: VisualDensity.compact,
              ),
            ),
            if (_testResult != null) ...[
              const SizedBox(width: 12),
              Flexible(
                child: Text(
                  _testResult == true
                      ? '✓ Connection works'
                      : '✗ ${_testError ?? "Connection failed"}',
                  style: TextStyle(
                    fontSize: 12,
                    color: _testResult == true
                        ? Colors.green
                        : tokens.colors.error,
                  ),
                ),
              ),
            ],
          ],
        ),
        const SizedBox(height: 32),
        Row(
          children: [
            OutlinedButton(
              onPressed: () => setState(() => _step = 0),
              style: OutlinedButton.styleFrom(
                visualDensity: VisualDensity.compact,
              ),
              child: const Text('Back', style: TextStyle(fontSize: 12)),
            ),
            const Spacer(),
            FilledButton(
              onPressed: _keyCtrl.text.trim().isEmpty ? null : _finish,
              child: const Text('Get Started'),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildDoneStep(EaTokens tokens) {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(
          _testResult == true ? Symbols.check_circle : Symbols.settings,
          size: 64,
          color: _testResult == true
              ? Colors.green
              : tokens.colors.textTertiary,
        ),
        const SizedBox(height: 24),
        Text(
          'You\'re all set!',
          style: tokens.typography.textTheme.headlineSmall?.copyWith(
            color: tokens.colors.textPrimary,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 12),
        Text(
          _testResult == true
              ? '${_providerName(_selectedProvider)} is connected and working.'
              : 'You can always configure more providers in Settings.',
          textAlign: TextAlign.center,
          style: tokens.typography.textTheme.bodyMedium?.copyWith(
            color: tokens.colors.textSecondary,
          ),
        ),
        const SizedBox(height: 32),
        FilledButton(
          onPressed: _finish,
          child: const Text('Start Using Assistant'),
        ),
      ],
    );
  }
}
