import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../models/subagent.dart';
import '../../providers/agent_provider.dart';
import '../../providers/subagent_provider.dart';
import '../../theme/app_theme.dart';
import 'widgets/ea_list_tile.dart';
import 'widgets/job_result_dialog.dart';
import 'widgets/tree_selector.dart';

final _nameRegex = RegExp(r'^[a-zA-Z0-9_-]+$');
const _nameValidationMessage =
    'Name can only contain letters, numbers, hyphens, and underscores';

class SubagentsPanel extends ConsumerStatefulWidget {
  const SubagentsPanel({super.key});

  @override
  ConsumerState<SubagentsPanel> createState() => _SubagentsPanelState();
}

class _SubagentsPanelState extends ConsumerState<SubagentsPanel> {
  int _loadSequence = 0;
  bool _initialized = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    final requestId = ++_loadSequence;
    await ref.read(subagentProvider.notifier).loadList();
    if (!mounted || requestId != _loadSequence) return;
    _initialized = true;
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<ChatState>(agentProvider, (prev, next) {
      if (prev?.status == ChatStatus.streaming &&
          next.status == ChatStatus.idle) {
        _load();
      }
    });

    final state = ref.watch(subagentProvider);

    return Container(
      color: context.tokens.colors.bgCanvas,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _buildHeader(state),
          Expanded(child: _buildBody(state)),
        ],
      ),
    );
  }

  Widget _buildHeader(SubagentPanelState state) {
    return Container(
      height: 52,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: context.tokens.colors.borderSubtle)),
      ),
      child: Row(
        children: [
          Icon(Symbols.robot_2, size: 18, color: context.tokens.colors.accent),
          const SizedBox(width: 8),
          Text(
            'Subagents',
            style: context.tokens.typography.textTheme.headlineMedium!.copyWith(fontSize: 15, color: context.tokens.colors.textPrimary),
          ),
          const Spacer(),
          if (state.loading && state.agents.isNotEmpty)
            const SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
          const SizedBox(width: 4),
          Text(
            '${state.agents.length}',
            style: context.tokens.typography.textTheme.bodySmall!.copyWith(color: context.tokens.colors.textTertiary),
          ),
          const SizedBox(width: 4),
          InkWell(
            onTap: _load,
            child: Icon(Symbols.refresh, size: 16, color: context.tokens.colors.textTertiary),
          ),
          const SizedBox(width: 8),
          InkWell(
            onTap: _showCreateDialog,
            child: Icon(Symbols.add, size: 18, color: context.tokens.colors.textTertiary),
          ),
        ],
      ),
    );
  }

  List<Widget> _skillChips(List<String>? skills) {
    if (skills == null || skills.isEmpty) return [];
    final chips = skills.take(3).map((s) => _ChipLabel(label: s));
    if (skills.length > 3) {
      return [...chips, _ChipLabel(label: '+${skills.length - 3}')];
    }
    return [...chips];
  }

  Widget _buildBody(SubagentPanelState state) {
    if (!_initialized && state.loading) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (state.error != null && state.agents.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                state.error!,
                style: context.tokens.typography.textTheme.bodySmall!.copyWith(color: context.tokens.colors.error),
              ),
              const SizedBox(height: 8),
              OutlinedButton(onPressed: _load, child: const Text('Retry')),
            ],
          ),
        ),
      );
    }
    if (state.agents.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Symbols.robot_2, size: 40, color: context.tokens.colors.textTertiary),
            const SizedBox(height: 8),
            Text(
              'No subagents yet',
              style: context.tokens.typography.textTheme.bodySmall!.copyWith(color: context.tokens.colors.textTertiary),
            ),
            const SizedBox(height: 4),
            Text(
              'Create one from the panel or via chat',
              style: context.tokens.typography.textTheme.bodySmall!.copyWith(
                color: context.tokens.colors.textTertiary,
                fontSize: 11,
              ),
            ),
          ],
        ),
      );
    }
    return ListView.builder(
      padding: EdgeInsets.zero,
      itemCount: state.agents.length,
      itemBuilder: (_, i) {
        final agent = state.agents[i];
        final runningJobs = state.activeJobs.values
            .where((j) => j.agentName == agent.name && !j.isTerminal)
            .toList();
        final hasRunning = runningJobs.isNotEmpty;
        final statusLabel = hasRunning
            ? (runningJobs.first.status == 'cancelling' ? 'cancelling' : 'running')
            : 'idle';
        final skillChips = _skillChips(agent.skills);
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            EaListTile(
              leading: Icon(Symbols.robot_2, size: 18, color: context.tokens.colors.accent),
              title: agent.name,
              subtitle: agent.description.isEmpty ? null : agent.description,
              chips: skillChips.isEmpty ? null : skillChips,
              trailingBadges: [
                _ScopeBadge(label: agent.scope),
                _StatusBadge(label: statusLabel),
              ],
              trailingActions: [
                if (hasRunning)
                  IconButton(
                    icon: Icon(Symbols.stop_circle, size: 16, color: context.tokens.colors.warning),
                    onPressed: () => ref
                        .read(subagentProvider.notifier)
                        .cancelJob(runningJobs.first.jobId),
                  )
                else
                  IconButton(
                    icon: Icon(Symbols.play_arrow, size: 16),
                    onPressed: () => _showStartDialog(agent),
                  ),
                IconButton(
                  icon: Icon(Symbols.edit, size: 16),
                  onPressed: () => _showEditDialog(agent),
                ),
                if (!hasRunning)
                  IconButton(
                    icon: Icon(Symbols.delete, size: 16),
                    onPressed: () => _confirmDelete(agent),
                  ),
              ],
              onTap: () => ref.read(subagentProvider.notifier).toggleAgentExpand(agent.name),
            ),
            if (state.expandedAgentName == agent.name) _buildJobSection(state, agent),
          ],
        );
      },
    );
  }

  Future<void> _showCreateDialog() async {
    final nameCtrl = TextEditingController();
    final descriptionCtrl = TextEditingController();
    final modelCtrl = TextEditingController();
    final systemPromptCtrl = TextEditingController();
    var maxLlmCalls = 50;
    var costLimitUsd = 1.0;
    var timeoutSeconds = 300;
    var submitting = false;
    String? nameError;
    Set<String> selectedTools = {};
    Set<String> selectedSkills = {};

    List<String>? allTools;
    List<String>? allSkills;
    try {
      allTools = await ref.read(apiClientProvider).listToolNames();
      selectedTools = allTools.toSet();
    } catch (_) {}
    try {
      final skillsJson = await ref.read(apiClientProvider).listSkills();
      allSkills = skillsJson.map((s) => s['name']?.toString() ?? '').where((n) => n.isNotEmpty).toList();
    } catch (_) {}
    if (!mounted) {
      nameCtrl.dispose();
      descriptionCtrl.dispose();
      modelCtrl.dispose();
      systemPromptCtrl.dispose();
      return;
    }

    final t = context.tokens;
    final created = await showDialog<bool>(
      context: context,
      builder: (ctx) => _DeferredPopScope(
        child: StatefulBuilder(
          builder: (ctx, setDialogState) => AlertDialog(
            title: Text('Create Subagent', style: t.typography.textTheme.titleLarge?.copyWith(color: t.colors.textPrimary)),
          content: SingleChildScrollView(
            child: SizedBox(
              width: 480,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const SizedBox(height: 8),
                  TextField(
                    controller: nameCtrl,
                    style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                    decoration: InputDecoration(
                      labelText: 'Name *',
                      hintText: 'my-researcher',
                      errorText: nameError,
                    ),
                    onChanged: (_) =>
                        setDialogState(() => nameError = null),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: descriptionCtrl,
                    style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                    decoration: const InputDecoration(
                      labelText: 'Description *',
                      hintText: 'Researches topics and summarizes',
                    ),
                    maxLines: 2,
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: modelCtrl,
                    style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                    decoration: const InputDecoration(
                      labelText: 'Model',
                      hintText: 'deepseek:deepseek-v4-flash',
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: systemPromptCtrl,
                    style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'System prompt (optional)',
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Text('Skills',
                        style: t.typography.textTheme.headlineMedium?.copyWith(
                          fontSize: 14, color: t.colors.textPrimary,
                        )),
                    ],
                  ),
                  const SizedBox(height: 8),
                  if (allSkills != null) ...[
                    SizedBox(
                      height: 180,
                      child: GroupedTreeSelector<String>(
                        groups: _groupSkills(allSkills),
                        selected: selectedSkills,
                        onChanged: (v) => setDialogState(() => selectedSkills = v),
                        searchHint: 'Filter skills...',
                      ),
                    ),
                    const SizedBox(height: 16),
                  ],
                  if (allTools != null) ...[
                    Row(
                      children: [
                        Text('Tools',
                          style: t.typography.textTheme.headlineMedium?.copyWith(
                            fontSize: 14, color: t.colors.textPrimary,
                          )),
                      ],
                    ),
                    const SizedBox(height: 8),
                    SizedBox(
                      height: 180,
                      child: GroupedTreeSelector<String>(
                        groups: _groupTools(allTools),
                        selected: selectedTools,
                        onChanged: (v) => setDialogState(() => selectedTools = v),
                        searchHint: 'Filter tools...',
                      ),
                    ),
                    const SizedBox(height: 14),
                  ],
                  ExpansionTile(
                    title: Text('Limits',
                      style: t.typography.textTheme.bodySmall?.copyWith(
                        color: t.colors.textSecondary,
                      )),
                    initiallyExpanded: false,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: _NumberField(
                              label: 'Max LLM calls',
                              value: maxLlmCalls,
                              onChanged: (v) => setDialogState(() => maxLlmCalls = v as int),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: _NumberField(
                              label: 'Cost limit (\$)',
                              value: costLimitUsd,
                              isDouble: true,
                              onChanged: (v) => setDialogState(() => costLimitUsd = v as double),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: _NumberField(
                              label: 'Timeout (s)',
                              value: timeoutSeconds,
                              onChanged: (v) => setDialogState(() => timeoutSeconds = v as int),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: submitting ? null : () => Navigator.pop(ctx, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: submitting
                  ? null
                  : () async {
                      final name = nameCtrl.text.trim();
                      final description = descriptionCtrl.text.trim();
                      if (name.isEmpty || description.isEmpty) return;
                      if (!_nameRegex.hasMatch(name)) {
                        setDialogState(
                          () => nameError = _nameValidationMessage,
                        );
                        return;
                      }
                      setDialogState(() => nameError = null);
                      setDialogState(() => submitting = true);
                      try {
                        await ref
                            .read(subagentProvider.notifier)
                            .createAgent(
                              name: name,
                              description: description,
                              model: modelCtrl.text.trim().isEmpty
                                  ? null
                                  : modelCtrl.text.trim(),
                              maxLlmCalls: maxLlmCalls,
                              costLimitUsd: costLimitUsd,
                              timeoutSeconds: timeoutSeconds,
                              systemPrompt: systemPromptCtrl.text.trim().isEmpty
                                  ? null
                                  : systemPromptCtrl.text.trim(),
                              tools: selectedTools.length ==
                                      (allTools?.length ?? 0)
                                  ? null
                                  : selectedTools.toList(),
                              skills: selectedSkills.isEmpty
                                  ? null
                                  : selectedSkills.toList(),
                            );
                        if (ctx.mounted) Navigator.pop(ctx, true);
                      } catch (e) {
                        if (!ctx.mounted) return;
                        setDialogState(() => submitting = false);
                        ScaffoldMessenger.of(ctx).showSnackBar(
                          SnackBar(content: Text('Create failed: $e')),
                        );
                      }
                    },
              child: submitting
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Create'),
            ),
          ],
        ),
      ),
      ),
    );

    nameCtrl.dispose();
    descriptionCtrl.dispose();
    modelCtrl.dispose();
    systemPromptCtrl.dispose();
    if (created == true) await _load();
  }

  Future<void> _showStartDialog(SubagentAgentDef agent) async {
    final taskCtrl = TextEditingController();
    var submitting = false;

    final t = context.tokens;
    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: Text('Start ${agent.name}', style: t.typography.textTheme.titleLarge?.copyWith(color: t.colors.textPrimary)),
          content: SizedBox(
            width: 400,
            child: TextField(
              controller: taskCtrl,
              autofocus: true,
              minLines: 3,
              maxLines: 6,
              style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
              decoration: const InputDecoration(
                labelText: 'Task',
                hintText: 'What should the subagent do?',
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: submitting ? null : () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: submitting
                  ? null
                  : () async {
                      final task = taskCtrl.text.trim();
                      if (task.isEmpty) return;
                      setDialogState(() => submitting = true);
                      try {
                        await ref
                            .read(subagentProvider.notifier)
                            .startJob(agent.name, task);
                        if (ctx.mounted) Navigator.pop(ctx);
                      } catch (e) {
                        if (!ctx.mounted) return;
                        setDialogState(() => submitting = false);
                        ScaffoldMessenger.of(ctx).showSnackBar(
                          SnackBar(content: Text('Start failed: $e')),
                        );
                      }
                    },
              child: submitting
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Start'),
            ),
          ],
        ),
      ),
    );

    taskCtrl.dispose();
  }

  Future<void> _showEditDialog(SubagentAgentDef agent) async {
    final descriptionCtrl = TextEditingController(text: agent.description);
    final modelCtrl = TextEditingController(text: agent.model ?? '');
    final systemPromptCtrl = TextEditingController(
      text: agent.systemPrompt ?? '',
    );
    var maxLlmCalls = agent.maxLlmCalls;
    var costLimitUsd = agent.costLimitUsd;
    var timeoutSeconds = agent.timeoutSeconds;
    var submitting = false;

    Set<String> selectedTools = agent.tools?.toSet() ?? {};
    Set<String> selectedSkills = agent.skills?.toSet() ?? {};

    List<String>? allTools;
    List<String>? allSkills;
    try {
      allTools = await ref.read(apiClientProvider).listToolNames();
      if (selectedTools.isEmpty) selectedTools = allTools.toSet();
    } catch (_) {}
    try {
      final skillsJson = await ref.read(apiClientProvider).listSkills();
      allSkills = skillsJson.map((s) => s['name']?.toString() ?? '').where((n) => n.isNotEmpty).toList();
    } catch (_) {}

    final t = context.tokens;
    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: Text('Edit ${agent.name}', style: t.typography.textTheme.titleLarge?.copyWith(color: t.colors.textPrimary)),
          content: SingleChildScrollView(
            child: SizedBox(
              width: 480,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const SizedBox(height: 8),
                  TextField(
                    controller: descriptionCtrl,
                    style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                    decoration: const InputDecoration(
                      labelText: 'Description',
                    ),
                    maxLines: 2,
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: modelCtrl,
                    style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                    decoration: const InputDecoration(
                      labelText: 'Model',
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: systemPromptCtrl,
                    style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'System prompt (optional)',
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Text('Skills',
                        style: t.typography.textTheme.headlineMedium?.copyWith(
                          fontSize: 14, color: t.colors.textPrimary,
                        )),
                    ],
                  ),
                  const SizedBox(height: 8),
                  if (allSkills != null) ...[
                    SizedBox(
                      height: 180,
                      child: GroupedTreeSelector<String>(
                        groups: _groupSkills(allSkills),
                        selected: selectedSkills,
                        onChanged: (v) => setDialogState(() => selectedSkills = v),
                        searchHint: 'Filter skills...',
                      ),
                    ),
                    const SizedBox(height: 16),
                  ],
                  if (allTools != null) ...[
                    Row(
                      children: [
                        Text('Tools',
                          style: t.typography.textTheme.headlineMedium?.copyWith(
                            fontSize: 14, color: t.colors.textPrimary,
                          )),
                      ],
                    ),
                    const SizedBox(height: 8),
                    SizedBox(
                      height: 180,
                      child: GroupedTreeSelector<String>(
                        groups: _groupTools(allTools),
                        selected: selectedTools,
                        onChanged: (v) => setDialogState(() => selectedTools = v),
                        searchHint: 'Filter tools...',
                      ),
                    ),
                    const SizedBox(height: 14),
                  ],
                  ExpansionTile(
                    title: Text('Limits',
                      style: t.typography.textTheme.bodySmall?.copyWith(
                        color: t.colors.textSecondary,
                      )),
                    initiallyExpanded: false,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: _NumberField(
                              label: 'Max LLM calls',
                              value: maxLlmCalls,
                              onChanged: (v) => setDialogState(() => maxLlmCalls = v as int),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: _NumberField(
                              label: 'Cost limit (\$)',
                              value: costLimitUsd,
                              isDouble: true,
                              onChanged: (v) => setDialogState(() => costLimitUsd = v as double),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: _NumberField(
                              label: 'Timeout (s)',
                              value: timeoutSeconds,
                              onChanged: (v) => setDialogState(() => timeoutSeconds = v as int),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: submitting ? null : () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: submitting
                  ? null
                  : () async {
                      setDialogState(() => submitting = true);
                      try {
                        await ref
                            .read(apiClientProvider)
                            .updateSubagent(
                              agent.name,
                              description: descriptionCtrl.text.trim(),
                              model: modelCtrl.text.trim().isEmpty
                                  ? null
                                  : modelCtrl.text.trim(),
                              systemPrompt: systemPromptCtrl.text.trim().isEmpty
                                  ? null
                                  : systemPromptCtrl.text.trim(),
                              maxLlmCalls: maxLlmCalls,
                              costLimitUsd: costLimitUsd,
                              timeoutSeconds: timeoutSeconds,
                              tools: selectedTools.length ==
                                      (allTools?.length ?? 0)
                                  ? null
                                  : selectedTools.toList(),
                              skills: selectedSkills.isEmpty
                                  ? null
                                  : selectedSkills.toList(),
                            );
                        if (ctx.mounted) Navigator.pop(ctx, true);
                        await _load();
                      } catch (e) {
                        if (!ctx.mounted) return;
                        setDialogState(() => submitting = false);
                        ScaffoldMessenger.of(ctx).showSnackBar(
                          SnackBar(content: Text('Update failed: $e')),
                        );
                      }
                    },
              child: submitting
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Save'),
            ),
          ],
        ),
      ),
    );

    descriptionCtrl.dispose();
    modelCtrl.dispose();
    systemPromptCtrl.dispose();
  }

  void _confirmDelete(SubagentAgentDef agent) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => _DeferredPopScope(
        child: AlertDialog(
          title: Text('Delete subagent?', style: context.tokens.typography.textTheme.titleLarge?.copyWith(color: context.tokens.colors.textPrimary)),
        content: Text(
          'Delete ${agent.name}? This will also cancel any running jobs.',
          style: context.tokens.typography.textTheme.bodyMedium?.copyWith(color: context.tokens.colors.textPrimary),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
      ),
    );
    if (confirmed != true || !mounted) return;
    try {
      await ref
          .read(subagentProvider.notifier)
          .deleteAgent(agent.name);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Delete failed: $e')));
    }
  }

  List<TreeSelectorGroup<String>> _groupSkills(List<String> skills) {
    final sorted = List<String>.from(skills)..sort();
    return [
      TreeSelectorGroup<String>(
        label: 'All Skills',
        items: sorted.map((s) => TreeSelectorItem(label: s, value: s)).toList(),
      ),
    ];
  }

  String _formatRelativeTime(String iso) {
    try {
      final dt = DateTime.parse(iso);
      final now = DateTime.now();
      final diff = now.difference(dt);
      if (diff.inSeconds < 60) return '${diff.inSeconds}s ago';
      if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
      if (diff.inHours < 24) return '${diff.inHours}h ago';
      if (diff.inDays < 7) return '${diff.inDays}d ago';
      return '${dt.month}/${dt.day}';
    } catch (_) {
      return '';
    }
  }

  Widget _buildJobSection(SubagentPanelState state, SubagentAgentDef agent) {
    final t = context.tokens;
    final terminalJobs = state.activeJobs.values
        .where((j) => j.agentName == agent.name && j.isTerminal)
        .toList()
      ..sort((a, b) => (b.createdAt ?? '').compareTo(a.createdAt ?? ''));

    return Container(
      decoration: BoxDecoration(
        color: t.colors.bgElevated,
        border: Border(bottom: BorderSide(color: t.colors.borderSubtle)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Divider(height: 1, color: t.colors.borderSubtle),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 8, 4),
            child: Row(
              children: [
                Text(
                  'Jobs (${terminalJobs.length})',
                  style: t.typography.textTheme.bodySmall?.copyWith(
                    color: t.colors.textSecondary,
                    fontSize: 11,
                  ),
                ),
                const Spacer(),
                if (terminalJobs.isNotEmpty)
                  TextButton(
                  onPressed: () => ref.read(subagentProvider.notifier).clearCompletedJobs(agent.name),
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                  child: Text(
                    'Clear completed',
                    style: t.typography.textTheme.bodySmall?.copyWith(
                      color: t.colors.accent,
                      fontSize: 11,
                    ),
                  ),
                ),
              ],
            ),
          ),
          if (terminalJobs.isEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
              child: Text(
                'No completed jobs',
                style: t.typography.textTheme.bodySmall?.copyWith(
                  color: t.colors.textTertiary,
                  fontSize: 11,
                ),
              ),
            )
          else
            ...terminalJobs.map((job) => _buildJobRow(job)),
        ],
      ),
    );
  }

  Widget _buildJobRow(SubagentJob job) {
    final t = context.tokens;
    final statusIcon = switch (job.status) {
      'completed' => Symbols.check_circle,
      'failed' => Symbols.error,
      'cancelled' => Symbols.warning,
      _ => Symbols.circle,
    };
    final statusColor = switch (job.status) {
      'completed' => t.colors.success,
      'failed' => t.colors.error,
      'cancelled' => t.colors.warning,
      _ => t.colors.textTertiary,
    };
    final durationText = job.createdAt != null
        ? _formatRelativeTime(job.createdAt!)
        : '';

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Row(
        children: [
          Icon(statusIcon, size: 14, color: statusColor),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  job.task,
                  style: t.typography.textTheme.bodySmall?.copyWith(
                    color: t.colors.textPrimary,
                    fontSize: 12,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                if (durationText.isNotEmpty)
                  Text(
                    durationText,
                    style: t.typography.textTheme.bodySmall?.copyWith(
                      color: t.colors.textTertiary,
                      fontSize: 10,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
            decoration: BoxDecoration(
              color: statusColor.withAlpha(18),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(
              job.status,
              style: TextStyle(fontSize: 10, color: statusColor, height: 1.2),
            ),
          ),
          const SizedBox(width: 6),
          TextButton(
            onPressed: () => showDialog(
              context: context,
              builder: (_) => JobResultDialog(job: job),
            ),
            style: TextButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: Text(
              'View',
              style: t.typography.textTheme.bodySmall?.copyWith(
                color: t.colors.accent,
                fontSize: 11,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ScopeBadge extends StatelessWidget {
  final String label;

  const _ScopeBadge({required this.label});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(left: 4),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: context.tokens.colors.accent.withAlpha(18),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        label == 'workspace'
            ? 'ws'
            : label == 'all'
                ? 'all'
                : 'user',
        style: context.tokens.typography.textTheme.bodySmall!.copyWith(
          fontSize: 10,
          color: context.tokens.colors.accent,
        ),
      ),
    );
  }
}

class _StatusBadge extends StatelessWidget {
  final String label;

  const _StatusBadge({required this.label});

  Color _color(EaTokens tokens) {
    return switch (label) {
      'running' => tokens.colors.success,
      'cancelling' => tokens.colors.warning,
      'completed' => tokens.colors.accent,
      'failed' => tokens.colors.error,
      'cancelled' => tokens.colors.warning,
      _ => tokens.colors.textTertiary,
    };
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final color = _color(tokens);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withAlpha(18),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        label,
        style: tokens.typography.textTheme.bodySmall!.copyWith(fontSize: 10, color: color),
      ),
    );
  }
}

class _NumberField extends StatelessWidget {
  final String label;
  final num value;
  final bool isDouble;
  final ValueChanged<num> onChanged;

  const _NumberField({
    required this.label,
    required this.value,
    this.isDouble = false,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: TextEditingController(text: value.toString())
        ..selection = TextSelection.collapsed(offset: value.toString().length),
      style: DefaultTextStyle.of(context).style.copyWith(fontSize: 13, color: context.tokens.colors.textPrimary),
      decoration: InputDecoration(
        labelText: label,
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
      ),
      keyboardType: TextInputType.numberWithOptions(
        decimal: isDouble,
        signed: false,
      ),
      onChanged: (s) {
        final parsed = isDouble ? double.tryParse(s) : int.tryParse(s);
        if (parsed != null && parsed > 0) onChanged(parsed);
      },
    );
  }
}

List<TreeSelectorGroup<String>> _groupTools(List<String> tools) {
  final groups = <String, List<String>>{};
  for (final tool in tools) {
    String prefix;
    if (tool.startsWith('mcp__')) {
      prefix = 'mcp';
    } else if (tool.contains('_')) {
      prefix = tool.split('_').first;
    } else {
      prefix = 'other';
    }
    groups.putIfAbsent(prefix, () => []);
    groups[prefix]!.add(tool);
  }
  final sortedKeys = groups.keys.toList()..sort();
  return sortedKeys.map((key) {
    final items = groups[key]!..sort();
    return TreeSelectorGroup<String>(
      label: key,
      items: items.map((t) {
        final prefix = '${key}_';
        final displayName = t.startsWith(prefix) ? t.substring(prefix.length) : t;
        return TreeSelectorItem(label: displayName, value: t);
      }).toList(),
    );
  }).toList();
}

class _ChipLabel extends StatelessWidget {
  final String label;
  const _ChipLabel({required this.label});

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: t.colors.accentMuted,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        label,
        style: t.typography.textTheme.bodySmall?.copyWith(
          fontSize: 10,
          color: t.colors.accent,
        ),
      ),
    );
  }
}

class _DeferredPopScope extends StatefulWidget {
  final Widget child;

  const _DeferredPopScope({required this.child});

  @override
  State<_DeferredPopScope> createState() => _DeferredPopScopeState();
}

class _DeferredPopScopeState extends State<_DeferredPopScope> {
  bool _deferred = false;

  @override
  Widget build(BuildContext context) {
    return PopScope<bool>(
      canPop: _deferred,
      onPopInvokedWithResult: (didPop, result) {
        if (_deferred && didPop) {
          _deferred = false;
          return;
        }
        if (didPop) return;
        setState(() => _deferred = true);
        FocusScope.of(context).unfocus();
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (context.mounted) Navigator.of(context).pop(result);
        });
      },
      child: widget.child,
    );
  }
}
