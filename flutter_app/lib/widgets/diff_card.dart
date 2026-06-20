import 'package:flutter/material.dart';
import '../theme/app_theme.dart';
import 'ea_card.dart';
import 'ea_button.dart';

enum DiffCardStatus { pending, accepted, rejected, failed }

class DiffCardData {
  final String suggestionId;
  final String filePath;
  final String diff;
  final DiffCardStatus status;
  final String? errorMessage;

  const DiffCardData({
    required this.suggestionId,
    required this.filePath,
    required this.diff,
    this.status = DiffCardStatus.pending,
    this.errorMessage,
  });

  DiffCardData copyWith({
    DiffCardStatus? status,
    String? errorMessage,
  }) {
    return DiffCardData(
      suggestionId: suggestionId,
      filePath: filePath,
      diff: diff,
      status: status ?? this.status,
      errorMessage: errorMessage ?? this.errorMessage,
    );
  }
}

class DiffCard extends StatefulWidget {
  final DiffCardData data;
  final void Function(DiffCardData data)? onAccept;
  final void Function(DiffCardData data)? onReject;
  final VoidCallback? onRerequest;

  const DiffCard({
    super.key,
    required this.data,
    this.onAccept,
    this.onReject,
    this.onRerequest,
  });

  @override
  State<DiffCard> createState() => _DiffCardState();
}

class _DiffCardState extends State<DiffCard>
    with SingleTickerProviderStateMixin {
  late AnimationController _fadeController;
  late Animation<double> _fadeAnimation;

  @override
  void initState() {
    super.initState();
    _fadeController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 500),
    );
    _fadeAnimation = CurvedAnimation(
      parent: _fadeController,
      curve: Curves.easeOut,
    );
  }

  @override
  void didUpdateWidget(covariant DiffCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.data.status != widget.data.status) {
      if (widget.data.status == DiffCardStatus.accepted) {
        _fadeController.forward();
      } else if (widget.data.status == DiffCardStatus.rejected) {
        _fadeController.forward();
      }
    }
  }

  @override
  void dispose() {
    _fadeController.dispose();
    super.dispose();
  }

  void _handleAccept() {
    widget.onAccept?.call(widget.data);
  }

  void _handleReject() {
    widget.onReject?.call(widget.data);
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;

    if (widget.data.status == DiffCardStatus.accepted) {
      return FadeTransition(
        opacity: _fadeAnimation,
        child: _buildAccepted(tokens),
      );
    }

    if (widget.data.status == DiffCardStatus.rejected) {
      return FadeTransition(
        opacity: _fadeAnimation,
        child: Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Symbols.close, size: 18, color: tokens.colors.error),
              const SizedBox(width: 6),
              Text(
                "Edit rejected",
                style: tokens.typography.textTheme.bodySmall?.copyWith(
                  color: tokens.colors.error,
                ),
              ),
            ],
          ),
        ),
      );
    }

    return AnimatedSize(
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeInOut,
      child: widget.data.status == DiffCardStatus.failed
          ? _buildError(tokens)
          : _buildPending(tokens),
    );
  }

  Widget _buildAccepted(EaTokens tokens) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Symbols.check_circle, size: 18, color: tokens.colors.success),
          const SizedBox(width: 6),
          Text(
            'Edit accepted',
            style: tokens.typography.textTheme.bodySmall
                ?.copyWith(color: tokens.colors.success),
          ),
        ],
      ),
    );
  }

  Widget _buildError(EaTokens tokens) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Icon(Symbols.error, size: 16, color: tokens.colors.error),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              widget.data.errorMessage ?? 'Failed to apply edit',
              style: tokens.typography.textTheme.bodySmall
                  ?.copyWith(color: tokens.colors.error),
            ),
          ),
          const SizedBox(width: 8),
          GestureDetector(
            onTap: widget.onRerequest,
            child: Text(
              'Re-request',
              style: tokens.typography.textTheme.labelMedium
                  ?.copyWith(color: tokens.colors.accent),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPending(EaTokens tokens) {
    final diffLines = widget.data.diff.split('\n');
    final List<_DiffLine> parsed = [];
    int lineNum = 0;

    for (final line in diffLines) {
      lineNum++;
      if (line.startsWith('+')) {
        parsed.add(_DiffLine(type: _DiffLineType.added, text: line.substring(1), lineNum: lineNum));
      } else if (line.startsWith('-')) {
        parsed.add(_DiffLine(type: _DiffLineType.removed, text: line.substring(1), lineNum: lineNum));
      } else if (line.startsWith('@@')) {
        parsed.add(_DiffLine(type: _DiffLineType.header, text: line, lineNum: lineNum));
      } else if (line.startsWith('diff --git') ||
          line.startsWith('index ') ||
          line.startsWith('--- ') ||
          line.startsWith('+++ ') ||
          line.trim().isEmpty) {
        continue;
      } else {
        parsed.add(_DiffLine(type: _DiffLineType.context, text: line, lineNum: lineNum));
      }
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: EaCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildHeader(tokens),
            const Divider(height: 1),
            _buildDiffBody(tokens, parsed),
            const Divider(height: 1),
            _buildActions(tokens),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader(EaTokens tokens) {
    return Padding(
      padding: EdgeInsets.all(tokens.spacing.sm),
      child: Row(
        children: [
          Icon(Symbols.description, size: 16, color: tokens.colors.textSecondary),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              widget.data.filePath,
              style: tokens.typography.monoTheme.bodySmall
                  ?.copyWith(color: tokens.colors.textSecondary),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildDiffBody(EaTokens tokens, List<_DiffLine> lines) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Container(
        constraints: BoxConstraints(
          maxHeight: 300,
          minWidth: double.infinity,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: lines.map((line) {
            return _buildDiffLine(tokens, line);
          }).toList(),
        ),
      ),
    );
  }

  Widget _buildDiffLine(EaTokens tokens, _DiffLine line) {
    Color bg;
    Color fg;
    String prefix;

    switch (line.type) {
      case _DiffLineType.added:
        bg = tokens.colors.success.withAlpha(25);
        fg = tokens.colors.success;
        prefix = '+';
      case _DiffLineType.removed:
        bg = tokens.colors.error.withAlpha(25);
        fg = tokens.colors.error;
        prefix = '-';
      case _DiffLineType.header:
        bg = tokens.colors.info.withAlpha(15);
        fg = tokens.colors.info;
        prefix = '';
      case _DiffLineType.context:
        bg = Colors.transparent;
        fg = tokens.colors.textTertiary;
        prefix = ' ';
    }

    final monoStyle = tokens.typography.monoTheme.bodySmall
        ?.copyWith(color: fg);

    return Container(
      width: double.infinity,
      color: bg,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 1),
      child: Text.rich(
        TextSpan(
          children: [
            if (prefix.isNotEmpty)
              TextSpan(
                text: prefix,
                style: monoStyle?.copyWith(fontWeight: FontWeight.w600),
              ),
            TextSpan(
              text: line.text,
              style: monoStyle,
            ),
          ],
        ),
        maxLines: 1,
        overflow: TextOverflow.clip,
      ),
    );
  }

  Widget _buildActions(EaTokens tokens) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        tokens.spacing.sm,
        tokens.spacing.xs,
        tokens.spacing.sm,
        tokens.spacing.sm,
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.end,
        children: [
          EaButton.secondary(
            label: 'Reject',
            onPressed: _handleReject,
          ),
          const SizedBox(width: 8),
          EaButton.primary(
            label: 'Accept',
            onPressed: _handleAccept,
          ),
        ],
      ),
    );
  }
}

enum _DiffLineType { added, removed, context, header }

class _DiffLine {
  final _DiffLineType type;
  final String text;
  final int lineNum;

  const _DiffLine({
    required this.type,
    required this.text,
    required this.lineNum,
  });
}
