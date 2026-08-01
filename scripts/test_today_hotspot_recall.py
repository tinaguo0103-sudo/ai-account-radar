from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run_daily_workflow import merge_exact_today_new_rows, normalize_page_owned_facts


class TodayHotspotRecallTest(unittest.TestCase):
    def test_exact_today_new_is_promoted_before_legacy_filter(self):
        run_id = "run_20260801_080215"
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            source_dir = run_dir / "sources"
            source_dir.mkdir()
            rows = [
                {
                    "候选时态": "today_new",
                    "首次发现批次": run_id,
                    "运行批次": run_id,
                    "内容标题": "DeepSeek V4 正式版",
                    "内容链接": "https://www.douyin.com/video/1",
                    "aweme_id": "1",
                    "published_at": "2026-07-31T08:00:00Z",
                    "likes": 1917,
                    "comments": 0,
                    "favorites": 12,
                    "shares": 3,
                },
                {
                    "候选时态": "today_new",
                    "首次发现批次": "run_wrong",
                    "运行批次": "run_wrong",
                    "内容标题": "wrong run",
                    "内容链接": "https://www.douyin.com/video/2",
                },
            ]
            (source_dir / "current_run_rows.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                encoding="utf-8",
            )
            output = merge_exact_today_new_rows(
                {"content_items": [], "candidates": []},
                run_dir=run_dir,
                run_id=run_id,
            )
        self.assertEqual(output["today_new_promotion"]["promoted_count"], 1)
        self.assertEqual(output["today_new_promotion"]["encountered_count"], 2)
        self.assertEqual(len(output["content_items"]), 1)
        self.assertEqual(len(output["candidates"]), 1)
        self.assertEqual(output["candidates"][0]["likes"], 1917)
        self.assertEqual(output["candidates"][0]["comments"], 0)
        self.assertEqual(
            output["today_new_promotion"]["exclusions"][0]["reason"],
            "today_new_wrong_run",
        )

    def test_missing_identity_is_typed_local_exclusion(self):
        run_id = "run_20260801_080215"
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            source_dir = run_dir / "sources"
            source_dir.mkdir()
            row = {
                "候选时态": "today_new",
                "首次发现批次": run_id,
                "运行批次": run_id,
                "内容标题": "没有链接",
            }
            (source_dir / "current_run_rows.jsonl").write_text(
                json.dumps(row, ensure_ascii=False), encoding="utf-8",
            )
            output = merge_exact_today_new_rows(
                {"content_items": [], "candidates": []},
                run_dir=run_dir,
                run_id=run_id,
            )
        self.assertEqual(output["candidates"], [])
        self.assertEqual(
            output["today_new_promotion"]["exclusions"][0]["reason"],
            "today_new_identity_missing",
        )

    def test_discovery_default_is_readable_query_portfolio(self):
        source = Path(__file__).with_name("douyin_video_discovery.mjs").read_text()
        for role in ("today_release", "real_test", "workflow_change", "creator_business"):
            self.assertIn(f'role: "{role}"', source)
        self.assertNotIn('deriveSearchQuery(captured, "AI 工具 人工智能"', source)
        producer = Path(__file__).with_name(
            "douyin_video_understanding_producer.py"
        ).read_text()
        self.assertNotIn('or "AI 工作流|AI 工具 实测|AI Agent 应用"', producer)

    def test_page_owned_media_identity_is_preserved_without_browser_resolution(self):
        facts = normalize_page_owned_facts({
            "aweme_id": "1",
            "create_time": 1785400000,
            "statistics": {
                "digg_count": 0, "comment_count": 2,
                "collect_count": 3, "share_count": 4,
            },
            "video": {
                "duration": 12000,
                "play_addr": {
                    "uri": "video-identity",
                    "url_list": ["https://media.example/video.mp4"],
                },
            },
        }, Path("raw.json"))
        self.assertEqual(facts["playable_url"], "https://media.example/video.mp4")
        self.assertEqual(facts["media_identity"], "video-identity")
        self.assertEqual(facts["likes"], 0)


if __name__ == "__main__":
    unittest.main()
