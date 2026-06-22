import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../models/subagent.dart';
import '../../providers/agent_provider.dart';
import '../../theme/app_theme.dart';
import 'widgets/ea_list_tile.dart';

class SkillsPanel extends ConsumerStatefulWidget {
  const SkillsPanel({super.key});

  @override
  ConsumerState<SkillsPanel> createState() => _SkillsPanelState();
}

class _SkillsPanelState extends ConsumerState<SkillsPanel> {
  List<Map<String, dynamic>> _skills = [];
  List<SubagentAgentDef>? _allAgents;
  bool _loading = true;
  String? _error;
  int _loadSequence = 0;

  @override
  void initState() {
    super.initState();
    _loadSkills();
  }

  Future<void> _loadSkills() async {
    if (!mounted) return;
    final requestId = ++_loadSequence;
    setState(() {
      _loading = _skills.isEmpty;
      _error = null;
    });
    try {
      final skills = await ref
          .read(apiClientProvider)
          .listSkills();
      if (!mounted || requestId != _loadSequence) return;
      List<SubagentAgentDef>? agents;
      try {
        final agentsJson = await ref
            .read(apiClientProvider)
            .listSubagents();
        agents = agentsJson
            .map((j) => SubagentAgentDef.fromJson(j as Map<String, dynamic>))
            .toList();
      } catch (_) {}

      if (!mounted || requestId != _loadSequence) return;
      setState(() {
        _skills = skills.whereType<Map<String, dynamic>>().toList();
        _allAgents = agents;
        _loading = false;
      });
    } catch (e) {
      if (!mounted || requestId != _loadSequence) return;
      setState(() {
        _error = 'Cannot load skills: $e';
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: context.tokens.colors.bgCanvas,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            height: 52,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: context.tokens.colors.borderSubtle)),
            ),
            child: Row(
              children: [
                Icon(Symbols.psychology, size: 18, color: context.tokens.colors.accent),
                const SizedBox(width: 8),
                Text(
                  'Skills',
                  style: context.tokens.typography.textTheme.headlineMedium!.copyWith(fontSize: 15, color: context.tokens.colors.textPrimary),
                ),
                const Spacer(),
                if (_loading && _skills.isNotEmpty)
                  const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                const SizedBox(width: 4),
                Text(
                  '${_skills.length}',
                  style: context.tokens.typography.textTheme.bodySmall!.copyWith(
                    color: context.tokens.colors.textTertiary,
                  ),
                ),
                const SizedBox(width: 4),
                InkWell(
                  onTap: _loadSkills,
                  child: Icon(
                    Symbols.refresh,
                    size: 16,
                    color: context.tokens.colors.textTertiary,
                  ),
                ),
                const SizedBox(width: 8),
                InkWell(
                  onTap: _showCreateDialog,
                  child: Icon(Symbols.add, size: 18, color: context.tokens.colors.textTertiary),
                ),
              ],
            ),
          ),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  int _usageCount(String skillName) {
    if (_allAgents == null) return 0;
    return _allAgents!.where((a) => a.skills?.contains(skillName) == true).length;
  }

  Widget _buildBody() {
    if (_loading && _skills.isEmpty) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (_error != null && _skills.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                _error!,
                style: context.tokens.typography.textTheme.bodySmall!.copyWith(color: context.tokens.colors.error),
              ),
              const SizedBox(height: 8),
              OutlinedButton(
                onPressed: _loadSkills,
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
      );
    }
    if (_skills.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Symbols.psychology, size: 40, color: context.tokens.colors.textTertiary),
            const SizedBox(height: 8),
            Text(
              'No skills',
              style: context.tokens.typography.textTheme.bodySmall!.copyWith(color: context.tokens.colors.textTertiary),
            ),
          ],
        ),
      );
    }
    return ListView.builder(
      padding: EdgeInsets.zero,
      itemCount: _skills.length,
      itemBuilder: (_, i) {
        final skill = _skills[i];
        final name = skill['name']?.toString() ?? '';
        final description = skill['description']?.toString() ?? '';
        final usageCount = _usageCount(name);
        return EaListTile(
          leading: Icon(Symbols.psychology, size: 18, color: context.tokens.colors.accent),
          title: name,
          subtitle: usageCount > 0 ? 'used by $usageCount agents' : (description.isNotEmpty ? description : null),
          trailingActions: [
            IconButton(
              icon: Icon(Symbols.edit, size: 16),
              onPressed: () => _showEditDialog(skill),
            ),
            IconButton(
              icon: Icon(Symbols.delete, size: 16),
              onPressed: () => _confirmDelete(skill),
            ),
          ],
          onTap: () => _showEditDialog(skill),
        );
      },
    );
  }

  Future<void> _showEditDialog(Map<String, dynamic> skill) async {
    final name = skill['name']?.toString() ?? '';
    if (name.isEmpty) return;
    final detail = await ref
        .read(apiClientProvider)
        .getSkillDetail(name);
    final descriptionCtrl = TextEditingController(
      text: detail['description']?.toString() ?? skill['description']?.toString() ?? '',
    );
    final contentCtrl = TextEditingController(
      text: detail['content']?.toString() ?? '',
    );
    var submitting = false;

    final t = context.tokens;
    final updated = await showDialog<bool>(
      context: context,
      builder: (ctx) => _DeferredPopScope(
        child: StatefulBuilder(
          builder: (ctx, setDialogState) => AlertDialog(
            title: Text('Edit $name', style: t.typography.textTheme.titleLarge?.copyWith(color: t.colors.textPrimary)),
            content: SingleChildScrollView(
              child: SizedBox(
                width: 520,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      controller: descriptionCtrl,
                      style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                      decoration: const InputDecoration(labelText: 'Description'),
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: contentCtrl,
                      style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                      minLines: 6,
                      maxLines: 10,
                      decoration: const InputDecoration(labelText: 'Content'),
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
                        setDialogState(() => submitting = true);
                        try {
                          await ref
                              .read(apiClientProvider)
                              .updateSkill(
                                name,
                                description: descriptionCtrl.text.trim(),
                                content: contentCtrl.text,
                              );
                          if (ctx.mounted) Navigator.pop(ctx, true);
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
      ),
    );

    descriptionCtrl.dispose();
    contentCtrl.dispose();
    if (updated == true) await _loadSkills();
  }

  Future<void> _confirmDelete(Map<String, dynamic> skill) async {
    final name = skill['name']?.toString() ?? '';
    if (name.isEmpty) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => _DeferredPopScope(
        child: AlertDialog(
          title: Text('Delete skill?', style: context.tokens.typography.textTheme.titleLarge?.copyWith(color: context.tokens.colors.textPrimary)),
          content: Text(
            'Delete $name?',
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
    if (!mounted) return;
    if (confirmed != true) return;
    try {
      await ref
          .read(apiClientProvider)
          .deleteSkill(name);
      await _loadSkills();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Delete failed: $e')));
    }
  }

  Future<void> _showCreateDialog() async {
    final nameCtrl = TextEditingController();
    final descriptionCtrl = TextEditingController();
    final contentCtrl = TextEditingController();
    var submitting = false;

    final t = context.tokens;
    final created = await showDialog<bool>(
      context: context,
      builder: (ctx) => _DeferredPopScope(
        child: StatefulBuilder(
          builder: (ctx, setDialogState) => AlertDialog(
            title: Text('New Skill', style: t.typography.textTheme.titleLarge?.copyWith(color: t.colors.textPrimary)),
            content: SingleChildScrollView(
              child: SizedBox(
                width: 520,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      controller: nameCtrl,
                      style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                      decoration: const InputDecoration(labelText: 'Name'),
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: descriptionCtrl,
                      style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                      decoration: const InputDecoration(labelText: 'Description'),
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: contentCtrl,
                      style: t.typography.textTheme.bodyLarge?.copyWith(color: t.colors.textPrimary),
                      minLines: 6,
                      maxLines: 10,
                      decoration: const InputDecoration(labelText: 'Content'),
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
                        if (name.isEmpty) return;
                        setDialogState(() => submitting = true);
                        try {
                          await ref
                              .read(apiClientProvider)
                              .createSkill(
                                name,
                                descriptionCtrl.text.trim(),
                                contentCtrl.text,
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
    contentCtrl.dispose();
    if (created == true) await _loadSkills();
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
