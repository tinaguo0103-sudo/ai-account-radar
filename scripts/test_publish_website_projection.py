import json
import tempfile
import unittest
from pathlib import Path

from daily_workflow import DailyWorkflow
from publish_website_projection import (
    ProjectionError,
    build_workflow_projection,
)


class WebsiteProjectionTest(unittest.TestCase):
    def test_video_understanding_is_bound_to_exact_content_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            run_id = "run_20260727_080000"
            flow.begin(run_id, "2026-07-27")
            package = {
                "source_url": "https://www.douyin.com/video/1",
                "status": "completed",
                "caption_timeline": [{"start": 1.0, "text": "字幕"}],
            }
            collection = {"content_items": [{
                "item_id": "douyin:1", "source": "douyin",
                "title": "title", "source_url": "https://www.douyin.com/video/1",
            }], "candidates": [], "source_runs": [],
                "understanding_results": [{"package": package}]}
            flow.commit_stage(
                run_id, "collection_enrichment", collection, "completed",
            )
            flow.commit_stage(run_id, "editorial", {"run_id": run_id, "topics": []}, "completed")
            flow.commit_stage(
                run_id, "scripts", {"run_id": run_id, "scripts": [], "failures": []}, "completed",
            )
            flow.complete(run_id, "completed", f"terminal:{run_id}")
            payload = build_workflow_projection(
                path, run_id, "qa-private"
            )
            self.assertEqual(payload["collected_items"][0]["video_understanding"], package)
            self.assertNotIn("payload_sha256", payload)
            self.assertEqual(
                payload["collected_items"][0]["video_understanding"]["caption_timeline"][0]["start"],
                1.0,
            )

    def test_nonterminal_workflow_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            run_id = "run_20260727_080000"
            flow.begin(run_id, "2026-07-27")
            with self.assertRaisesRegex(ProjectionError, "workflow_terminal_not_committed"):
                build_workflow_projection(path, run_id, "qa-private")

    def test_source_ledger_is_preserved_when_legacy_source_runs_are_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            run_id = "run_20260729_080000"
            flow.begin(run_id, "2026-07-29")
            collection = {
                "content_items": [],
                "candidates": [],
                "source_runs": [],
                "source_ledger": [
                    {
                        "source": "configured_account", "attempted": True,
                        "status": "completed", "discovered_count": 2,
                        "reason": "", "captured_at": "2026-07-29T00:00:00Z",
                    },
                    {
                        "source": "recommendation", "attempted": True,
                        "status": "completed_empty", "discovered_count": 0,
                        "reason": "no_safe_visible_candidates",
                        "captured_at": "2026-07-29T00:00:01Z",
                    },
                    {
                        "source": "dynamic_search", "attempted": True,
                        "status": "completed", "discovered_count": 16,
                        "reason": "", "captured_at": "2026-07-29T00:00:02Z",
                    },
                ],
                "understanding_results": [],
            }
            flow.commit_stage(run_id, "collection_enrichment", collection, "completed")
            flow.commit_stage(run_id, "editorial", {"run_id": run_id, "topics": []}, "completed")
            flow.commit_stage(
                run_id, "scripts", {"run_id": run_id, "scripts": [], "failures": []},
                "completed",
            )
            flow.complete(run_id, "completed", f"terminal:{run_id}")
            payload = build_workflow_projection(path, run_id, "qa-private")
            self.assertEqual(
                [row["source"] for row in payload["source_runs"]],
                ["configured_account", "recommendation", "dynamic_search"],
            )
            self.assertEqual(
                [row["item_count"] for row in payload["source_runs"]], [2, 0, 16],
            )
            self.assertEqual(
                payload["source_runs"][1]["error_summary"],
                "no_safe_visible_candidates",
            )


if __name__ == "__main__":
    unittest.main()
