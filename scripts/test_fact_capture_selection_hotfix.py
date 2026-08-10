from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import content_sampler
import douyin_video_understanding_producer as producer
import run_daily_workflow as workflow


class FactCaptureSelectionHotfixTests(unittest.TestCase):
    def test_configured_shape_preserves_real_zero_and_business_aliases(self):
        row = producer.normalize_candidate_shape({
            "来源链接": "https://www.douyin.com/video/7667000000000000001",
            "原始来源标题": "AI workflow",
            "账号名/公众号名": "ordinary account",
            "发布时间": "2026-07-30T01:00:00Z",
            "likes": 0,
            "comments": 0,
            "favorites": 0,
            "shares": 0,
        })
        self.assertEqual(row["aweme_id"], "7667000000000000001")
        self.assertEqual(row["title"], "AI workflow")
        self.assertEqual(row["author"], "ordinary account")
        self.assertEqual(
            [row[key] for key in ("likes", "comments", "favorites", "shares")],
            [0, 0, 0, 0],
        )

    def test_direct_collection_facts_win_without_detail_resolver(self):
        row = {
            "来源链接": "https://www.douyin.com/video/7667000000000000002",
            "发布时间": "2026-07-30T02:00:00Z",
            "likes": 11,
            "comments": 0,
            "favorites": 2,
            "shares": 1,
            "fact_provenance": {"capture": "configured_account_page_owned_works_response"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            adapted = workflow.adapt_collection_rows([row], run_dir=Path(tmp))[0]
        self.assertEqual(adapted["published_at"], "2026-07-30T02:00:00Z")
        self.assertEqual(
            [adapted[key] for key in ("likes", "comments", "favorites", "shares")],
            [11, 0, 2, 1],
        )
        self.assertNotIn("page_owned_payload_not_available", json.dumps(adapted))

    def test_one_base_package_associates_every_real_angle(self):
        run_id = "run_20260730_120000"
        base = {
            "run_id": run_id,
            "business_date": "2026-07-30",
            "content_items": [{
                "source_url": "https://www.douyin.com/video/7667000000000000003",
                "aweme_id": "7667000000000000003",
                "title": "AI",
            }],
            "candidates": [
                {
                    "source_url": "https://www.douyin.com/video/7667000000000000003",
                    "aweme_id": "7667000000000000003",
                    "主题聚类ID": angle,
                    "title": "AI",
                    "discovery_source": "configured_account",
                    "likes": 100,
                    "published_at": "2026-07-30T00:00:00Z",
                }
                for angle in ("angle-a", "angle-b")
            ],
        }
        package = {
            "run_id": run_id,
            "aweme_id": "7667000000000000003",
            "source_url": "https://www.douyin.com/video/7667000000000000003",
            "status": "completed",
        }
        args = Namespace(
            run_id=run_id, qa_frozen_packages="", video_mode="normal",
        )
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame.jpg"
            frame.write_bytes(b"qa-keyframe")
            package["keyframes"] = [{
                "path": str(frame),
                "time_second": 0,
                "sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
            }]
            with mock.patch.object(
                workflow, "produce", return_value={"packages": [package], "failures": []},
            ) as produced:
                enriched = workflow.enrich(args, base)
        self.assertEqual(produced.call_count, 1)
        self.assertEqual(len(enriched["understanding_results"]), 1)
        self.assertEqual(
            {row["base_item_id"] for row in enriched["understanding_results"]},
            {"douyin:7667000000000000003"},
        )
        self.assertEqual(
            enriched["candidates"][0]["legacy_candidate_ids"],
            [
                "douyin:7667000000000000003::angle:angle-a",
                "douyin:7667000000000000003::angle:angle-b",
            ],
        )

    def test_static_heat_no_longer_contributes_to_score(self):
        source = Path(content_sampler.__file__).read_text(encoding="utf-8")
        self.assertNotIn("heat = 3", source)
        self.assertNotIn("heat * 20 / 5", source)

    def test_release_contract_requires_candidate_specific_reasons(self):
        release = json.loads(
            (Path(__file__).parents[1] / "config/web010_single_daily_workflow_release.json")
            .read_text(encoding="utf-8")
        )
        protocol = " ".join(release["externalSchedule"]["outerAgentProtocol"])
        self.assertIn("candidate's own available fact", protocol)
        self.assertIn("do not reuse generic batch reasons", protocol)
        self.assertIn("title value, persona fit, and exploration stay separate", protocol)

    def test_discovery_uses_bounded_multi_direction_query_ledger(self):
        source = (
            Path(__file__).with_name("douyin_video_discovery.mjs")
            .read_text(encoding="utf-8")
        )
        self.assertIn("AI 工作流|AI 工具 实测|AI Agent 应用", source)
        self.assertIn(".slice(0, 4)", source)
        self.assertIn("query_ledger: queryLedger", source)
        self.assertIn('status: normalizedSearch.length ? "completed" : "completed_empty"', source)


if __name__ == "__main__":
    unittest.main()
