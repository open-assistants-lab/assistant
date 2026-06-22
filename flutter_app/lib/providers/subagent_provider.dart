import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/subagent.dart';
import '../services/api_client.dart';
import 'agent_provider.dart';

class SubagentPanelState {
  final List<SubagentAgentDef> agents;
  final Map<String, SubagentJob> activeJobs;
  final bool loading;
  final String? error;
  final int loadSequence;
  final String? expandedAgentName;

  const SubagentPanelState({
    this.agents = const [],
    this.activeJobs = const {},
    this.loading = false,
    this.error,
    this.loadSequence = 0,
    this.expandedAgentName,
  });

  SubagentPanelState copyWith({
    List<SubagentAgentDef>? agents,
    Map<String, SubagentJob>? activeJobs,
    bool? loading,
    Object? error = _errorSentinel,
    int? loadSequence,
    String? Function()? expandedAgentName,
  }) {
    return SubagentPanelState(
      agents: agents ?? this.agents,
      activeJobs: activeJobs ?? this.activeJobs,
      loading: loading ?? this.loading,
      error: identical(error, _errorSentinel) ? this.error : error as String?,
      loadSequence: loadSequence ?? this.loadSequence,
      expandedAgentName: expandedAgentName != null ? expandedAgentName() : this.expandedAgentName,
    );
  }

  static const _errorSentinel = Object();
}

class SubagentNotifier extends StateNotifier<SubagentPanelState> {
  static const int _maxTerminalJobs = 10;

  final Ref _ref;
  Timer? _pollTimer;

  SubagentNotifier(this._ref) : super(const SubagentPanelState());

  ApiClient get _api => _ref.read(apiClientProvider);

  Future<void> loadList() async {
    final seq = state.loadSequence + 1;
    state = state.copyWith(loading: state.agents.isEmpty, loadSequence: seq);
    try {
      final raw = await _api.listSubagents();
      if (seq != state.loadSequence) {
        return;
      }
      final agents = raw
          .whereType<Map<String, dynamic>>()
          .map((e) => SubagentAgentDef.fromJson(e))
          .toList();
      state = state.copyWith(agents: agents, loading: false);
    } catch (e) {
      if (seq != state.loadSequence) return;
      state = state.copyWith(error: 'Cannot load subagents: $e', loading: false);
    }
  }

  Future<void> createAgent({
    required String name,
    required String description,
    String? model,
    String scope = 'user',
    List<String>? tools,
    List<String>? skills,
    String? systemPrompt,
    int maxLlmCalls = 50,
    double costLimitUsd = 1.0,
    int timeoutSeconds = 300,
  }) async {
    await _api.createSubagent(
      name: name,
      description: description,
      model: model,
      scope: scope,
      tools: tools,
      skills: skills,
      systemPrompt: systemPrompt,
      maxLlmCalls: maxLlmCalls,
      costLimitUsd: costLimitUsd,
      timeoutSeconds: timeoutSeconds,
    );
    await loadList();
  }

  Future<void> deleteAgent(String name) async {
    await _api.deleteSubagent(name);
    await loadList();
  }

  Future<String> startJob(String agentName, String task) async {
    final result = await _api.startSubagent(agentName, task: task);
    final jobId = result['job_id']?.toString() ?? '';
    if (jobId.isNotEmpty) {
      final job = SubagentJob(
        jobId: jobId,
        agentName: agentName,
        task: task,
        status: 'pending',
      );
      state = state.copyWith(
        activeJobs: {...state.activeJobs, jobId: job},
      );
      _pruneTerminalJobs();
      _ensurePolling();
    }
    return jobId;
  }

  Future<void> cancelJob(String jobId) async {
    await _api.cancelSubagentJob(jobId);
    _updateJob(jobId, status: 'cancelling');
  }

  Future<void> instructJob(String jobId, String instruction) async {
    await _api.instructSubagentJob(jobId, instruction: instruction);
  }

  void _updateJob(String jobId,
      {String? status,
      Map<String, dynamic>? progress,
      String? result,
      String? error,
      List<Map<String, dynamic>>? instructions}) {
    final existing = state.activeJobs[jobId];
    if (existing == null) return;
    final updated = SubagentJob(
      jobId: existing.jobId,
      agentName: existing.agentName,
      task: existing.task,
      status: status ?? existing.status,
      progress: progress ?? existing.progress,
      result: result ?? existing.result,
      error: error ?? existing.error,
      instructions: instructions ?? existing.instructions,
      createdAt: existing.createdAt,
      startedAt: existing.startedAt,
      completedAt: existing.completedAt,
    );
    final jobs = Map<String, SubagentJob>.from(state.activeJobs);
    jobs[jobId] = updated;
    state = state.copyWith(activeJobs: jobs);
    _pruneTerminalJobs();
  }

  void toggleAgentExpand(String agentName) {
    if (state.expandedAgentName == agentName) {
      state = state.copyWith(expandedAgentName: () => null);
    } else {
      state = state.copyWith(expandedAgentName: () => agentName);
    }
  }

  void _pruneTerminalJobs() {
    final agentJobs = <String, List<MapEntry<String, SubagentJob>>>{};
    final active = <String, SubagentJob>{};
    for (final entry in state.activeJobs.entries) {
      if (entry.value.isTerminal) {
        agentJobs.putIfAbsent(entry.value.agentName, () => []);
        agentJobs[entry.value.agentName]!.add(entry);
      } else {
        active[entry.key] = entry.value;
      }
    }
    final kept = <String, SubagentJob>{};
    for (final group in agentJobs.entries) {
      final sorted = group.value
        ..sort((a, b) => (a.value.createdAt ?? '')
            .compareTo(b.value.createdAt ?? ''));
      final limited = sorted.skip(
        sorted.length > _maxTerminalJobs ? sorted.length - _maxTerminalJobs : 0,
      );
      for (final e in limited) {
        kept[e.key] = e.value;
      }
    }
    state = state.copyWith(activeJobs: {...active, ...kept});
  }

  void clearCompletedJobs(String agentName) {
    final remaining = <String, SubagentJob>{};
    for (final entry in state.activeJobs.entries) {
      if (entry.value.agentName == agentName && entry.value.isTerminal) {
        continue;
      }
      remaining[entry.key] = entry.value;
    }
    state = state.copyWith(activeJobs: remaining);
  }

  Future<void> pollJob(String jobId) async {
    final existing = state.activeJobs[jobId];
    if (existing == null || existing.isTerminal) return;
    try {
      final data = await _api.getSubagentJob(
        jobId,
      );
      final job = data['job'] as Map<String, dynamic>? ?? data;
      _updateJob(
        jobId,
        status: job['status']?.toString(),
        progress: job['progress'] as Map<String, dynamic>?,
        result: job['result']?.toString(),
        error: job['error']?.toString(),
        instructions: job['instructions'] != null
            ? List<Map<String, dynamic>>.from(job['instructions'] as List)
            : null,
      );
    } catch (_) {}
  }

  void _ensurePolling() {
    if (_pollTimer != null && _pollTimer!.isActive) return;
    _pollTimer = Timer.periodic(const Duration(seconds: 2), (_) async {
      final running = <String>[];
      for (final entry in state.activeJobs.entries) {
        if (!entry.value.isTerminal) {
          running.add(entry.key);
        }
      }
      if (running.isEmpty) {
        _pollTimer?.cancel();
        _pollTimer = null;
        _pruneTerminalJobs();
        return;
      }
      for (final jobId in running) {
        await pollJob(jobId);
      }
      _pruneTerminalJobs();
    });
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }
}

final subagentProvider =
    StateNotifierProvider<SubagentNotifier, SubagentPanelState>((ref) {
  return SubagentNotifier(ref);
});
