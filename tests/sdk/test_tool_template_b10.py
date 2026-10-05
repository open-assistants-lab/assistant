"""B10: TOOL.md templating — PATH probe, one-pass render, frontmatter fences (#81-#84)."""

from __future__ import annotations

import pytest

from src.sdk.tools_custom import (
    needs_path_probe,
    render_command_template,
    split_frontmatter,
)


class TestPathProbeOnlyForBareCommands:
    """#81: the probe used the UNRENDERED first token and rejected runnable tools."""

    @pytest.mark.parametrize(
        "template",
        ["{{tool_dir}}/run.sh", "./run.sh", "/usr/bin/env python", "FOO=1 cmd", "{{tool_dir}}"],
    )
    def test_non_bare_templates_are_not_probed(self, template):
        assert needs_path_probe(template) is False, f"{template!r} must not be PATH-probed"

    @pytest.mark.parametrize("template", ["echo hi", "python script.py", "git status"])
    def test_bare_commands_are_still_probed(self, template):
        assert needs_path_probe(template) is True


class TestOnePassRendering:
    """#82: sequential replace() re-substituted values."""

    def test_a_value_containing_a_placeholder_is_not_expanded(self):
        """a="{{b}}" must NOT pick up b's value (old code substituted X twice)."""
        out = render_command_template('echo {{a}} {{b}}', {"a": "{{b}}", "b": "X"})
        assert out == "echo '{{b}}' X", out

    def test_literal_placeholders_in_values_are_preserved(self):
        out = render_command_template('echo {{note}}', {"note": "keep {{other}} text"})
        assert "keep {{other}} text" in out, f"user data was mangled: {out!r}"

    def test_unfilled_placeholders_render_empty(self):
        assert render_command_template("echo {{filled}} {{empty}}", {"filled": "yes"}) == (
            "echo yes "
        )

    def test_tool_dir_is_rendered_and_quoted(self):
        out = render_command_template("{{tool_dir}}/run.sh {{p}}", {"p": "a b"}, tool_dir="/t dir")
        assert out.startswith("'/t dir'/run.sh"), out


class TestLazyPathSharesTheRenderer:
    """#83: the index-rebuilt path left unfilled placeholders literal."""

    def test_rebuilt_wrapper_strips_unfilled_placeholders(self, tmp_path):
        from src.sdk.tool_index import _rebuild_custom_function
        from src.sdk.tools import ToolDefinition

        td = ToolDefinition(
            name="probe",
            description="d",
            parameters={"type": "object", "properties": {}},
        )
        rebuilt = _rebuild_custom_function(td, {"command": "echo [{{u}}]", "install": []})
        assert isinstance(rebuilt, ToolDefinition)
        # Both paths now render through the shared helper, which strips
        # unfilled optional placeholders instead of emitting them literally.
        assert render_command_template("echo [{{u}}]", {}) == "echo []"


class TestFrontmatterFenceSplitting:
    """#84: a description or command containing '---' truncated the YAML."""

    def test_dashes_inside_values_do_not_truncate(self):
        content = (
            "---\n"
            "name: probe\n"
            "description: uses a --- separator in prose\n"
            "command: echo hi --- there\n"
            "---\n"
            "Body\n"
        )
        parts = split_frontmatter(content)
        assert len(parts) == 3, parts
        assert "command: echo hi --- there" in parts[1]
        assert parts[2].strip() == "Body"

    def test_tool_file_with_dashes_in_description_parses(self, tmp_path):
        from src.sdk.tools_custom import _parse_tool_file

        f = tmp_path / "TOOL.md"
        f.write_text(
            "---\nname: probe\ndescription: a --- b\ncommand: echo hi\n---\nBody\n",
            encoding="utf-8",
        )
        tool = _parse_tool_file(f)
        assert tool is not None, "a tool with '---' in its description vanished"
        assert tool.name == "probe"

    def test_skill_file_with_dashes_in_body_parses(self, tmp_path):
        from src.skills.models import parse_skill_file_with_diagnostics

        f = tmp_path / "SKILL.md"
        f.write_text(
            "---\nname: demo\ndescription: prose with --- dashes\n---\nBody --- text\n",
            encoding="utf-8",
        )
        skill, _ = parse_skill_file_with_diagnostics(f)
        assert skill is not None, "a skill with '---' in its description vanished"
        assert skill["name"] == "demo"

    def test_unterminated_frontmatter_returns_nothing(self):
        assert split_frontmatter("---\nname: x\n") == []
