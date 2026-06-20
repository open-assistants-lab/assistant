import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import '../../../theme/app_theme.dart';
import '../../../models/message.dart';
import 'message_bubble.dart';
import 'streaming_bubble.dart';
import 'reasoning_bubble.dart';
import 'tool_call_card.dart';
import 'empty_state.dart';
import 'skill_load_banner.dart';
import '../../../widgets/diff_card.dart';

const _kScrollBottom = '__bottom__';

class ChatMessageList extends StatefulWidget {
  final List<ChatMessage> messages;
  final bool isStreaming;
  final String streamingText;
  final String reasoningText;
  final List<ToolCallDisplay> activeToolCalls;
  final List<String> skillsLoaded;
  final List<DiffCardData> diffCards;
  final void Function(DiffCardData data)? onDiffCardAccept;
  final void Function(DiffCardData data)? onDiffCardReject;
  final VoidCallback? onDiffCardRerequest;
  final ScrollController scrollController;
  final Widget? header;
  final bool isLoading;
  final WidgetBuilder? emptyBuilder;
  final EdgeInsetsGeometry? padding;
  final Map<String, GlobalKey>? messageKeys;

  const ChatMessageList({
    super.key,
    required this.messages,
    this.isStreaming = false,
    this.streamingText = '',
    this.reasoningText = '',
    this.activeToolCalls = const [],
    this.skillsLoaded = const [],
    this.diffCards = const [],
    this.onDiffCardAccept,
    this.onDiffCardReject,
    this.onDiffCardRerequest,
    required this.scrollController,
    this.header,
    this.isLoading = false,
    this.emptyBuilder,
    this.padding,
    this.messageKeys,
  });

  @override
  State<ChatMessageList> createState() => ChatMessageListState();

  static String get bottomSentinel => _kScrollBottom;
}

class ChatMessageListState extends State<ChatMessageList> {
  @override
  void initState() {
    super.initState();
    _ensureKeys();
  }

  @override
  void didUpdateWidget(covariant ChatMessageList oldWidget) {
    super.didUpdateWidget(oldWidget);
    _ensureKeys();
  }

  void _ensureKeys() {
    if (widget.messageKeys == null) return;
    for (final msg in widget.messages) {
      // Always create fresh GlobalKeys so reused IDs from a previous workspace
      // don't carry detached contexts.
      widget.messageKeys![msg.id] = GlobalKey();
    }
  }

  String? getVisibleAnchorMessage() {
    if (!widget.scrollController.hasClients) return null;
    if (widget.messageKeys == null) return null;

    final position = widget.scrollController.position;
    // With reverse: true, pixels==0 is the BOTTOM, maxScrollExtent is the TOP.
    if (position.extentBefore == 0.0) return _kScrollBottom;

    final viewportTop = position.pixels;
    final viewportBottom = position.pixels + position.viewportDimension;

    String? lastVisible;
    for (final msg in widget.messages) {
      final key = widget.messageKeys![msg.id];
      if (key == null) continue;
      final ctx = key.currentContext;
      if (ctx == null) continue;
      final box = ctx.findRenderObject() as RenderBox?;
      if (box == null || !box.hasSize) continue;
      final vp = RenderAbstractViewport.of(box);
      final revealed = vp.getOffsetToReveal(box, 0.0).offset;
      final msgTop = revealed;
      final msgBottom = msgTop + box.size.height;
      if (msgBottom > viewportTop && msgTop < viewportBottom) {
        lastVisible = msg.id;
      }
    }
    return lastVisible;
  }

  Future<void> scrollToMessage(String messageId) async {
    if (messageId == _kScrollBottom) {
      // Bottom = pixels 0 with reverse: true
      if (widget.scrollController.hasClients) {
        widget.scrollController.jumpTo(0);
      }
      return;
    }
    final key = widget.messageKeys?[messageId];
    if (key?.currentContext == null) return;
    await Scrollable.ensureVisible(
      key!.currentContext!,
      alignment: 0.0,
      duration: const Duration(milliseconds: 50),
    );
  }

  @override
  Widget build(BuildContext context) {
    final itemCount = _itemCount();

    if (itemCount == 0 && widget.isLoading) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }

    if (itemCount == 0) {
      if (widget.emptyBuilder != null) {
        return widget.emptyBuilder!(context);
      }
      return const ChatEmptyState();
    }

    return ListView.builder(
      controller: widget.scrollController,
      reverse: true,
      padding: widget.padding ??
          const EdgeInsets.symmetric(
            horizontal: AppSpacing.screenEdge,
            vertical: AppSpacing.cardPadding,
          ),
      itemCount: itemCount,
      itemBuilder: (context, index) => _buildItemAt(context, index),
    );
  }

  int _itemCount() {
    final extras =
        (widget.reasoningText.isNotEmpty ? 1 : 0) +
        (widget.isStreaming && widget.streamingText.isNotEmpty ? 1 : 0) +
        widget.activeToolCalls.length +
        widget.skillsLoaded.length +
        widget.diffCards.length;
    return widget.messages.length + (widget.header != null ? 1 : 0) + extras;
  }

  Widget _buildItemAt(BuildContext context, int index) {
    // With reverse: true, index 0 is the BOTTOM of the viewport.
    // We lay out items in reverse order so the latest message is at index 0.
    final extras = <Widget>[];
    if (widget.isStreaming && widget.streamingText.isNotEmpty) {
      extras.add(StreamingBubble(text: widget.streamingText));
    }
    if (widget.reasoningText.isNotEmpty) {
      extras.add(ReasoningBubble(content: widget.reasoningText));
    }
    for (final dc in widget.diffCards) {
      extras.add(DiffCard(
        data: dc,
        onAccept: widget.onDiffCardAccept,
        onReject: widget.onDiffCardReject,
        onRerequest: widget.onDiffCardRerequest,
      ));
    }
    for (final tc in widget.activeToolCalls) {
      extras.add(ToolCallCard(toolCall: tc));
    }
    for (final name in widget.skillsLoaded.reversed) {
      extras.add(SkillLoadBanner(name: name));
    }
    // Build the reversed list: extras (newest streaming) first, then messages
    // in reverse order, then header at the very top of the reversed list.
    final allItems = <Widget>[
      ...extras,
      ...widget.messages.reversed.map((msg) {
        final gKey = widget.messageKeys?[msg.id];
        return KeyedSubtree(
          key: ValueKey('msg_${msg.id}_${msg.content.hashCode}'),
          child: _keyedWrapper(gKey, MessageBubble(message: msg)),
        );
      }),
      if (widget.header != null) widget.header!,
    ];
    return allItems[index];
  }

  Widget _keyedWrapper(GlobalKey? key, Widget child) {
    return key != null ? KeyedSubtree(key: key, child: child) : child;
  }
}
