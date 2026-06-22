"""Tests for research tools."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.sdk.research import ExperimentResult


class TestResearchTools:
    def test_research_list_empty(self):
        """research_list returns 'not found' when no experiments exist."""
        from src.sdk.tools_core.research import research_list

        result = research_list.invoke({
            "user_id": "test_user",
        })

        assert "No research experiments found" in result

    @patch("src.storage.paths.DataPaths")
    def test_research_list_with_results(self, mock_datapaths):
        """research_list reads from existing results.tsv."""
        from src.sdk.tools_core.research import research_list

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_instance = MagicMock()
            mock_instance.research_dir.return_value = Path(tmpdir)
            mock_datapaths.return_value = mock_instance

            tsv = Path(tmpdir) / "results.tsv"
            tsv.write_text(
                "commit\tdirty\tval_metric\tmemory_gb\tstatus\tdescription\n"
                "abc1234\t0\t0.950000\t0.5\tkeep\tBetter prompt\n"
            )

            result = research_list.invoke({
                "user_id": "t_user",
            })
            assert "abc1234" in result
            assert "0.950000" in result
            assert "Better prompt" in result
            assert "keep" in result

    def test_format_result(self):
        """_format_result produces expected string."""
        from src.sdk.tools_core.research import _format_result

        r = ExperimentResult(
            target_name="test",
            metric_value=0.85,
            improved=True,
            commit_hash="def5678",
            description="Improved specificity",
            status="keep",
        )
        formatted = _format_result(r)
        assert "Improved specificity" in formatted
        assert "0.8500" in formatted
        assert "def5678" in formatted
        assert "improved" in formatted
