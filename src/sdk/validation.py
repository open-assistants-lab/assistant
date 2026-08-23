"""Schema normalization and tool call argument repair.

normalize_tool_schema: Forces JSON schemas into LLM-friendly shape.
repair_tool_call: Attempts to fix malformed JSON from LLM tool call arguments.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from src.sdk.tools import ToolDefinition


def normalize_tool_schema(schema: dict[Any, Any]) -> dict[Any, Any]:
    """Normalize a JSON schema for LLM consumption.

    Applies:
        - Force additionalProperties: false on all objects
        - Convert oneOf → anyOf (wider LLM support)
        - Inline $ref references from a definitions/#defs block
        - Strip None/null defaults
    """
    if not isinstance(schema, dict):
        return schema

    # Copy first (audit B17): the caller may reuse its schema dict after
    # normalization; popping $defs/definitions from it is a side effect.
    schema = dict(schema)
    defs: dict[Any, Any] = {}
    for key in ("$defs", "definitions"):
        if key in schema:
            defs.update(schema.pop(key))

    result = _normalize_node(schema, defs)
    return cast(dict[Any, Any], result)


def _normalize_node(node: Any, defs: dict[Any, Any]) -> Any:
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        ref_path = node["$ref"]
        ref_name = ref_path.rsplit("/", 1)[-1]
        if ref_name in defs:
            resolved = dict(defs[ref_name])
            for k, v in node.items():
                if k != "$ref":
                    resolved[k] = v
            return _normalize_node(resolved, defs)
        return node

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key == "oneOf":
            result["anyOf"] = [_normalize_node(v, defs) for v in value]
        elif key == "default" and value is None:
            continue
        elif key in ("properties", "patternProperties"):
            result[key] = {k: _normalize_node(v, defs) for k, v in value.items()}
        elif key in ("anyOf", "allOf", "prefixItems", "items"):
            if isinstance(value, list):
                result[key] = [_normalize_node(v, defs) for v in value]
            else:
                result[key] = _normalize_node(value, defs)
        elif key == "additionalProperties":
            result[key] = value
        else:
            result[key] = value

    if result.get("type") == "object" and "additionalProperties" not in result:
        result["additionalProperties"] = False

    if "required" in result:
        req = result["required"]
        if isinstance(req, list):
            props = result.get("properties", {})
            result["required"] = [r for r in req if r in props]

    return result


def repair_tool_call(raw_args: str, tool_def: ToolDefinition | None = None) -> dict[Any, Any]:
    """Attempt to repair malformed JSON from LLM tool call arguments.

    Tries in order:
        1. json.loads() directly
        2. Fix trailing commas
        3. Fix single quotes → double quotes
        4. Extract JSON from markdown code fences
        5. Return {} if all fail
    """
    if not raw_args or not raw_args.strip():
        return {}

    cleaned = raw_args.strip()

    try:
        return cast(dict[Any, Any], json.loads(cleaned))
    except (json.JSONDecodeError, TypeError):
        pass

    fixed = _fix_trailing_commas(cleaned)
    try:
        return cast(dict[Any, Any], json.loads(fixed))
    except (json.JSONDecodeError, TypeError):
        pass

    fixed = _fix_single_quotes(cleaned)
    try:
        return cast(dict[Any, Any], json.loads(fixed))
    except (json.JSONDecodeError, TypeError):
        pass

    extracted = _extract_json_from_fence(cleaned)
    if extracted is not None:
        try:
            return cast(dict[Any, Any], json.loads(extracted))
        except (json.JSONDecodeError, TypeError):
            pass

    return {}


def _fix_trailing_commas(s: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", s)


def _fix_single_quotes(s: str) -> str:
    if '"' not in s and "'" in s:
        return s.replace("'", '"')
    return s


def _extract_json_from_fence(s: str) -> str | None:
    patterns: list[str] = [
        r"```(?:json)?\s*\n?(.*?)\n?\s*```",
        r"`([^`]+)`",
    ]
    for pat in patterns:
        m = re.search(pat, s, re.DOTALL)
        if m:
            return m.group(1).strip()
    brace_start = s.find("{")
    bracket_start = s.find("[")
    if brace_start >= 0 or bracket_start >= 0:
        if brace_start < 0:
            start = bracket_start
        elif bracket_start < 0:
            start = brace_start
        else:
            start = min(brace_start, bracket_start)
        if s[start] == "{":
            end = s.rfind("}")
        else:
            end = s.rfind("]")
        if end > start:
            return s[start : end + 1]
    return None
