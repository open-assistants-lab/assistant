import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../theme/app_theme.dart';
import '../../../models/message.dart';
import '../../../widgets/app_input.dart';
import '../../../providers/agent_provider.dart';
import '../../../providers/draft_provider.dart';
import '../../../providers/chat_tab_provider.dart';
import 'model_switcher.dart';

class ChatInput extends ConsumerStatefulWidget {
  const ChatInput({super.key});

  @override
  ConsumerState<ChatInput> createState() => _ChatInputState();
}

class _ChatInputState extends ConsumerState<ChatInput> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  String _sessionId = 'default';

  @override
  void initState() {
    super.initState();
    _sessionId = ref.read(activeChatTabProvider);
    final draft = ref.read(draftProvider.notifier).load(_sessionId);
    if (draft != null) _controller.text = draft;
  }

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<String>(activeChatTabProvider, (prev, next) {
      if (prev == next) return;
      if (prev != null) {
        ref.read(draftProvider.notifier).save(prev, _controller.text);
      }
      _sessionId = next;
      final draft = ref.read(draftProvider.notifier).load(next) ?? '';
      _controller.value = TextEditingValue(
        text: draft,
        selection: TextSelection.collapsed(offset: draft.length),
      );
    });

    final tokens = context.tokens;
    final state = ref.watch(agentProvider);
    final isSending = state.status == ChatStatus.streaming ||
        state.status == ChatStatus.awaitingApproval;
    final hasPendingApprovals = state.pendingApprovals.isNotEmpty;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (hasPendingApprovals) _ApprovalBar(
          pendingApprovals: state.pendingApprovals,
          ref: ref,
        ),
        AppChatField(
          controller: _controller,
          focusNode: _focusNode,
          hint: state.connected ? 'Ask anything...' : 'Connecting...',
          enabled: state.connected,
          sending: isSending,
          onChanged: (text) {
            ref.read(draftProvider.notifier).save(_sessionId, text);
          },
          onSend: (text) {
            ref.read(draftProvider.notifier).clear(_sessionId);
            ref.read(agentProvider.notifier).sendMessage(text);
          },
          onCancel: isSending
              ? () => ref.read(agentProvider.notifier).cancelExecution()
              : null,
          onReconnect: !state.connected
              ? () => ref.read(agentProvider.notifier).connect()
              : null,
        ),
        Padding(
          padding: EdgeInsets.fromLTRB(
            tokens.spacing.lg,
            tokens.spacing.xs * 2,
            tokens.spacing.lg,
            tokens.spacing.md,
          ),
          child: Row(children: const [ModelSwitcher(), Spacer()]),
        ),
      ],
    );
  }
}

class _ApprovalBar extends StatelessWidget {
  final Map<String, ToolCallDisplay> pendingApprovals;
  final WidgetRef ref;

  const _ApprovalBar({
    required this.pendingApprovals,
    required this.ref,
  });

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final approvals = pendingApprovals.values.toList();
    return Container(
      margin: EdgeInsets.fromLTRB(
        tokens.spacing.lg,
        tokens.spacing.xs,
        tokens.spacing.lg,
        tokens.spacing.xs,
      ),
      padding: EdgeInsets.all(tokens.spacing.lg),
      decoration: BoxDecoration(
        color: tokens.colors.bgSurface,
        borderRadius: BorderRadius.circular(tokens.radius.md),
        border: Border.all(color: tokens.colors.warning.withValues(alpha: 0.4)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(Symbols.warning, size: 16, color: tokens.colors.warning),
              SizedBox(width: tokens.spacing.sm),
              Text(
                'Tool requires approval',
                style: tokens.typography.textTheme.bodySmall?.copyWith(
                  color: tokens.colors.warning,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          SizedBox(height: tokens.spacing.sm),
          ...approvals.map((tc) => Padding(
            padding: EdgeInsets.only(bottom: tokens.spacing.xs),
            child: Text(
              tc.toolName,
              style: tokens.typography.textTheme.headlineMedium?.copyWith(fontSize: 15, color: tokens.colors.textPrimary),
            ),
          )),
          SizedBox(height: tokens.spacing.sm),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () {
                    for (final entry in approvals) {
                      ref.read(agentProvider.notifier).rejectToolCall(entry.callId);
                    }
                  },
                  style: OutlinedButton.styleFrom(
                    foregroundColor: tokens.colors.error,
                    side: BorderSide(color: tokens.colors.error),
                    padding: EdgeInsets.symmetric(vertical: tokens.spacing.sm + 2),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(tokens.radius.lg),
                    ),
                  ),
                  child: const Text('Reject', style: TextStyle(fontSize: 13)),
                ),
              ),
              SizedBox(width: tokens.spacing.sm),
              Expanded(
                child: FilledButton(
                  onPressed: () {
                    for (final entry in approvals) {
                      ref.read(agentProvider.notifier).approveToolCall(entry.callId);
                    }
                  },
                  style: FilledButton.styleFrom(
                    backgroundColor: tokens.colors.accent,
                    padding: EdgeInsets.symmetric(vertical: tokens.spacing.sm + 2),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(tokens.radius.lg),
                    ),
                  ),
                  child: const Text('Approve', style: TextStyle(fontSize: 13)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
