"""Utility for applying unified diffs with context validation."""


def apply_unified_diff(original: str, diff_lines: list[str]) -> str | None:
    """Apply a unified diff with context validation.

    Returns the new content on success, or None if context lines don't match
    (meaning the file was edited since the diff was generated).

    Expected format (agent-generated, no hunk headers needed):
      - old line
      + new line
       unchanged context line
    """
    orig = [] if original == "" else original.split("\n")
    result: list[str] = []
    i = 0

    for dline in diff_lines:
        if not dline:  # truly empty separator line between hunks, skip
            continue

        if dline.startswith("+"):
            result.append(dline[1:])
        elif dline.startswith("-"):
            # Verify context: the removed line must match
            if i < len(orig) and dline[1:] != orig[i]:
                return None  # context mismatch — file was edited
            i += 1
        elif dline.startswith(" "):
            # Verify context: unchanged line must match
            if i >= len(orig) or dline[1:] != orig[i]:
                return None  # context mismatch
            result.append(orig[i])
            i += 1
        else:
            return None  # malformed diff line

    # Append remaining original lines
    while i < len(orig):
        result.append(orig[i])
        i += 1

    return "\n".join(result)
