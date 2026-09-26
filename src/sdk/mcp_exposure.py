"""Resolve which MCP tools may be direct (exposure Axis B).

One axis, borrowed from Pi's `directTools` ladder and OpenCode's cheap glob
disable. This module is pure: it takes tools and configuration and returns a
decision. It performs no I/O, opens no connections, and never consults
permission policy — the governance floor is a separate, explicit step
(`apply_capability_floor`) so that filtering can never widen access.

Resolution order, later steps win:

1. server ``enabled`` (handled by the caller — a disabled server is not started)
2. the coarse ``tools.disabled`` glob gate
3. global ``include_tools`` then ``exclude_tools`` (Pi applies exclude after
   include; a name matching both is excluded)
4. per-server ``include_tools`` / ``exclude_tools`` (narrowing only)
5. the capability floor (I5) — disabled tools are absent, never deferred
6. ``exposure`` decides whether the survivors are direct, search-activated, or
   proxy-only
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Literal

Mode = Literal["always", "search", "never"]

#: v0.6.21 values, accepted for one release and reported as deprecated.
LEGACY_EXPOSURE: dict[str, Mode] = {
    "direct": "always",
    "hybrid": "search",
    "proxy": "never",
}

#: Every accepted configured value mapped onto Axis B, legacy keys included.
EXPOSURE_MODES: dict[str, Mode] = {
    "always": "always",
    "search": "search",
    "never": "never",
    **LEGACY_EXPOSURE,
}

#: `auto` is Claude Code's `auto:10`; a percentage of the context window is the
#: right unit because the same value must mean the same thing on an 8k local
#: model and a 200k hosted one.
AUTO_DEFAULT_PCT = 10.0


def parse_auto(setting: str) -> float | None:
    """Return the threshold percentage for an ``auto`` setting, else ``None``.

    A malformed value returns ``None`` rather than a default: ``auto:abc`` must
    not quietly become 10%.
    """
    value = (setting or "").strip()
    if value == "auto":
        return AUTO_DEFAULT_PCT
    if not value.startswith("auto:"):
        return None
    try:
        pct = float(value.split(":", 1)[1])
    except ValueError:
        return None
    if pct <= 0:
        return None
    return pct


def measure_mode(
    tools: Sequence[Any],
    *,
    model: str,
    threshold_pct: float = AUTO_DEFAULT_PCT,
    cache_warm: bool = True,
    defer_when_unknown: bool = True,
    window_override: int | None = None,
) -> tuple[Mode, str]:
    """Decide ``always`` vs ``search`` by measuring against the context window.

    The comparison is inclusive: at exactly the threshold we defer, matching
    Claude Code's rule that deferral activates *when* the definitions reach the
    percentage.

    Two cases resolve to ``search`` deliberately:

    * **cold cache** (C2) — a catalogue we have never costed against must not
      be injected whole. This is the opposite of Pi's
      ``deferWithMissingMetadata: false`` default, and the deviation is
      recorded in the design spec so it is not "corrected" by assumption. An
      operator who prefers Pi's tradeoff sets
      ``mcp.defer_with_missing_metadata: false``, which passes
      ``defer_when_unknown=False`` and measures regardless.
    * **unknown context window** — the same argument: we cannot cost the
      catalogue, so we do not inject it.
    """
    from src.sdk.context_measurement import (
        estimate_tool_schema_tokens,
        resolve_context_window,
    )

    if not cache_warm and defer_when_unknown:
        return "search", "metadata cache is cold; deferring until definitions are known"

    window = window_override or resolve_context_window(model)
    if not window or window <= 0:
        return "search", f"context window unknown for {model!r}; deferring"

    cost = estimate_tool_schema_tokens(list(tools))
    pct = (cost / window) * 100
    if pct >= threshold_pct:
        return "search", f"definitions {pct:.1f}% of window >= {threshold_pct:g}%"
    return "always", f"definitions {pct:.1f}% of window < {threshold_pct:g}%"


def matches_any(name: str, patterns: Iterable[str] | None) -> bool:
    """Simple glob match: ``*`` and ``?`` wildcard, everything else literal."""
    if not patterns:
        return False
    return any(fnmatchcase(name, str(pattern)) for pattern in patterns)


@dataclass(frozen=True)
class ModeDecision:
    """Result of mapping a configured ``exposure`` string onto Axis B."""

    mode: Mode
    setting: str
    deprecated: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ExposureDecision:
    """Which tools survive filtering, and how they should be exposed."""

    mode: Mode
    setting: str
    survivors: frozenset[str] = frozenset()
    excluded: dict[str, str] = field(default_factory=dict)
    always_load: frozenset[str] = frozenset()
    deprecated: bool = False
    error: str | None = None
    #: Why `auto` chose this mode; None for explicitly configured settings.
    measurement_reason: str | None = None


def resolve_mode(setting: str) -> ModeDecision:
    """Map a configured exposure value onto a mode, failing closed."""
    value = (setting or "").strip()
    if value in EXPOSURE_MODES:
        return ModeDecision(
            mode=EXPOSURE_MODES[value],
            setting=value,
            deprecated=value in LEGACY_EXPOSURE,
        )
    # An unrecognised value must never fall through to a permissive default:
    # that is how a renamed governance key silently dropped approval gating (#44).
    return ModeDecision(
        mode="never",
        setting=value,
        error=(
            f"unknown mcp.exposure {setting!r}; expected one of "
            f"{sorted(EXPOSURE_MODES)} or 'auto'. Failing closed to 'never'."
        ),
    )


def filter_survivors(
    tools: Sequence[Any],
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    server_exclude: Sequence[str] = (),
    disabled_globs: Sequence[str] = (),
) -> tuple[list[Any], dict[str, str]]:
    """Apply the ordered filters, returning (survivors, reason-per-excluded-name)."""
    excluded: dict[str, str] = {}
    survivors: list[Any] = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        if matches_any(name, disabled_globs):
            excluded[name] = "disabled by tools configuration"
            continue
        if include and not matches_any(name, include):
            excluded[name] = "not included"
            continue
        if matches_any(name, exclude):
            excluded[name] = "excluded"
            continue
        if matches_any(name, server_exclude):
            excluded[name] = "excluded by server"
            continue
        survivors.append(tool)
    return survivors, excluded


def _is_always_load_meta(meta: Any) -> bool:
    if meta is None:
        return False
    if isinstance(meta, dict):
        return bool(meta.get("alwaysLoad") or meta.get("always_load"))
    return bool(getattr(meta, "alwaysLoad", False) or getattr(meta, "always_load", False))


def resolve_always_load(
    tools: Sequence[Any],
    *,
    operator: Sequence[str] = (),
    server_trust: bool = False,
) -> frozenset[str]:
    """Resolve exempt-from-deferral tools.

    Server-supplied ``meta.alwaysLoad`` is honoured only under explicit operator
    trust (spec C3): a third-party server must not be able to grant itself a
    permanent slot in every context window.
    """
    selected = {str(name) for name in operator if name}
    if server_trust:
        selected.update(
            str(getattr(tool, "name", ""))
            for tool in tools
            if _is_always_load_meta(getattr(tool, "meta", None))
        )
    return frozenset(name for name in selected if name)


def apply_capability_floor(
    survivors: frozenset[str], caps: dict[str, Any]
) -> frozenset[str]:
    """I5: a capability-disabled tool is absent, not deferred.

    Filtering removes tools from the *active* set; if that path could ever add
    back a `scope=none` tool, deferral would become a way to smuggle a disabled
    tool into the model's context. This floor only ever removes.
    """
    tools_cfg = caps.get("tools") if isinstance(caps, dict) else None
    if not isinstance(tools_cfg, dict):
        return survivors
    return frozenset(name for name in survivors if tools_cfg.get(name) is not False)


def resolve_exposure(
    *,
    setting: str,
    tools: Sequence[Any],
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    server_exclude: Sequence[str] = (),
    disabled_globs: Sequence[str] = (),
    caps: dict[str, Any] | None = None,
    operator_always_load: Sequence[str] = (),
    server_trust: bool = False,
    measured_mode: Mode | None = None,
    measurement_reason: str | None = None,
) -> ExposureDecision:
    """Resolve one loop's MCP exposure from configuration and a tool catalogue.

    For an ``auto`` setting the caller must supply ``measured_mode``, produced
    by :func:`measure_mode` against the durable cache. An unmeasured ``auto`` is
    a configuration error rather than a silent assumption, because defaulting it
    to ``always`` would inject an uncosted catalogue and defaulting it to
    ``never`` would hide working tools. Explicitly configured settings ignore
    ``measured_mode`` entirely — the operator's choice wins.
    """
    auto_pct = parse_auto(setting)
    if auto_pct is not None:
        if measured_mode is None:
            return ExposureDecision(
                mode="search",
                setting=setting,
                error=(
                    f"mcp.exposure {setting!r} requires a measured decision; "
                    "call measure_mode() against the metadata cache first."
                ),
            )
        mode_decision = ModeDecision(mode=measured_mode, setting=setting)
    else:
        mode_decision = resolve_mode(setting)

    kept, excluded = filter_survivors(
        tools,
        include=include,
        exclude=exclude,
        server_exclude=server_exclude,
        disabled_globs=disabled_globs,
    )
    names = frozenset(str(getattr(tool, "name", "")) for tool in kept)
    names = apply_capability_floor(names, caps or {})

    if mode_decision.mode == "never":
        # Nothing is promoted, so nothing reaches the context as a direct tool.
        return ExposureDecision(
            mode="never",
            setting=setting,
            survivors=frozenset(),
            excluded=excluded,
            always_load=resolve_always_load(
                kept, operator=operator_always_load, server_trust=server_trust
            ),
            deprecated=mode_decision.deprecated,
            error=mode_decision.error,
            measurement_reason=measurement_reason,
        )

    return ExposureDecision(
        mode=mode_decision.mode,
        setting=setting,
        survivors=names,
        excluded=excluded,
        always_load=resolve_always_load(
            kept, operator=operator_always_load, server_trust=server_trust
        ),
        deprecated=mode_decision.deprecated,
        error=mode_decision.error,
        measurement_reason=measurement_reason,
    )
