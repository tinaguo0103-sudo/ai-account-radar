from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daily_workflow import DailyWorkflow, STAGES
from publish_website_projection import ProjectionError, build_workflow_projection, source_name


class SourceProjectionTests(unittest.TestCase):
    def test_known_platforms_are_not_folded_into_aihot(self) -> None:
        self.assertEqual("public_web", source_name("OpenAI", "feishu_recvl4uBak96UM"))
        self.assertEqual("public_web", source_name("Anthropic", "feishu_recvl4uBakM7LI"))
        self.assertEqual("x", source_name("X", "feishu_recvl4uBakTUDT"))
        self.assertEqual("wechat", source_name("微信公众号", "feishu_recvlm0QvTIDMe"))
        self.assertEqual("aihot", source_name("AIHOT", "feishu_recvl4uBak1aBQ"))
        with self.assertRaisesRegex(ProjectionError, "unknown_source_platform"):
            source_name("unregistered platform")

    def test_projection_preserves_exact_item_provenance_and_stable_source_keys(self) -> None:
        run_id = "run_20261006_081500"
        business_date = "2026-10-06"
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(database)
            flow.begin(run_id, business_date)
            flow.commit_stage("run_20261006_081500", STAGES[0], {
                "run_id": run_id,
                "content_items": [{
                    "item_id": "url:https://openai.com/news/release",
                    "source": "OpenAI", "source_id": "feishu_recvl4uBak96UM",
                    "source_key": "openai_news", "account": "OpenAI",
                    "title": "Release", "summary": "Body", "body": "Body",
                    "source_url": "https://openai.com/news/release",
                    "published_at": "2026-10-05", "source_provenance": [
                        {"source_id": "feishu_recvl4uBak96UM", "source": "openai_news",
                         "url": "https://openai.com/news/release", "raw_artifact_path": "/private/tmp/raw.txt"},
                        {"source_id": "feishu_recvl4uBakM7LI", "source": "anthropic_newsroom",
                         "url": "https://openai.com/news/release"},
                    ],
                }],
                "source_runs": [
                    {"source": "openai_news", "status": "completed", "planned_count": 1,
                     "succeeded_count": 1, "failed_count": 0, "item_count": 1},
                    {"source": "x", "status": "not_attempted", "planned_count": 0,
                     "succeeded_count": 0, "failed_count": 0, "item_count": 0},
                    {"source": "douyin", "status": "partial", "planned_count": 31,
                     "succeeded_count": 28, "failed_count": 3, "item_count": 12},
                ],
            }, "completed")
            flow.commit_stage(run_id, STAGES[1], {"run_id": run_id, "topics": []}, "completed")
            flow.commit_stage(run_id, STAGES[2], {"run_id": run_id, "scripts": []}, "completed")
            payload = build_workflow_projection(database, run_id, "qa-private", artifact_root=tmp)

        item = payload["collected_items"][0]
        self.assertEqual("public_web", item["source"])
        self.assertEqual(
            {"feishu_recvl4uBak96UM", "feishu_recvl4uBakM7LI"},
            {row["source_id"] for row in item["source_provenance"]},
        )
        self.assertNotIn("raw_artifact_path", item["source_provenance"][0])
        runs = {row["source"]: row for row in payload["source_runs"]}
        self.assertEqual({"openai_news", "x", "douyin"}, set(runs))
        self.assertEqual("not_attempted", runs["x"]["status"])
        self.assertEqual((31, 28, 3), (
            runs["douyin"]["planned_count"], runs["douyin"]["succeeded_count"],
            runs["douyin"]["failed_count"],
        ))


if __name__ == "__main__":
    unittest.main()
