import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from run_daily_workflow import (
    adapt_collection_rows,
    main,
    normalize_collection_candidates,
    normalize_items,
    validate_editorial,
)


RUN_ID = "run_20260730_120000"


def content(url: str, title: str = "AI workflow") -> dict:
    return {
        "平台": "抖音",
        "内容标题": title,
        "内容链接": url,
    }


def candidate(url: str, cluster: str, title: str) -> dict:
    return {
        "平台": "抖音",
        "来源链接": url,
        "主题聚类ID": cluster,
        "我的选题标题": title,
        "原始来源标题": title,
    }


class CollectionAdapterTests(unittest.TestCase):
    def test_source_link_alias_maps_to_canonical_content(self):
        rows = adapt_collection_rows(
            [candidate("https://www.douyin.com/video/12345678901", "a", "A")],
            run_dir=Path("/missing"),
        )
        self.assertEqual(
            rows[0]["source_url"],
            "https://www.douyin.com/video/12345678901",
        )
        self.assertEqual(rows[0]["aweme_id"], "12345678901")

    def test_same_url_same_angle_merges_but_distinct_angles_survive(self):
        url = "https://www.douyin.com/video/12345678901"
        items, _ = normalize_items([content(url)])
        rows = [
            candidate(url, "cluster-a", "Angle A"),
            candidate(url, "cluster-a", "Angle A"),
            candidate(url, "cluster-b", "Angle B"),
        ]
        normalized, video, failures = normalize_collection_candidates(
            rows, items=items, run_id=RUN_ID,
        )
        self.assertEqual(len(normalized), 2)
        self.assertEqual(len(video), 1)
        self.assertEqual(failures, [])
        self.assertEqual(sum(row["merged_input_count"] for row in normalized), 3)
        self.assertEqual(
            {row["主题聚类ID"] for row in normalized},
            {"cluster-a", "cluster-b"},
        )

    def test_malformed_candidate_is_item_local(self):
        url = "https://www.douyin.com/video/12345678901"
        items, _ = normalize_items([content(url)])
        normalized, _, failures = normalize_collection_candidates(
            [{}, candidate(url, "cluster-a", "Angle A")],
            items=items,
            run_id=RUN_ID,
        )
        self.assertEqual(len(normalized), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0]["reason"], "collection_candidate_content_mapping_missing",
        )

    def test_unknown_engagement_stays_null_with_typed_reason(self):
        rows = adapt_collection_rows(
            [content("https://www.douyin.com/video/12345678901")],
            run_dir=Path("/missing"),
        )
        self.assertIsNone(rows[0]["likes"])
        self.assertIsNone(rows[0]["comments"])
        self.assertIsNone(rows[0]["favorites"])
        self.assertIsNone(rows[0]["shares"])
        self.assertEqual(
            rows[0]["fact_missing_reasons"]["likes"],
            "page_owned_payload_not_available",
        )

    def test_all_65_candidates_require_editorial_output(self):
        candidates = [
            {"candidate_id": f"candidate-{index}"} for index in range(65)
        ]
        result = {
            "run_id": RUN_ID,
            "topics": [
                {"candidate_id": row["candidate_id"], "decision": "observe"}
                for row in candidates
            ],
        }
        validate_editorial(RUN_ID, result, candidates)
        with self.assertRaisesRegex(Exception, "editorial_result_coverage_incomplete"):
            validate_editorial(RUN_ID, {**result, "topics": result["topics"][:-1]}, candidates)


class NormalEntrypointDiscoveryTests(unittest.TestCase):
    def test_normal_public_command_owns_discovery_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "collection.json"
            url = "https://www.douyin.com/video/12345678901"
            fixture.write_text(json.dumps({
                "run_id": RUN_ID,
                "business_date": "2026-07-30",
                "status": "completed",
                "content_items": [content(url)],
                "candidates": [
                    {
                        **candidate(url, "configured", "Configured AI angle"),
                        "discovery_source": "configured_account",
                        "likes": 1000,
                        "published_at": "2026-07-30T04:00:00Z",
                    },
                ],
                "configured_account_status": "completed",
                "configured_account_reason": "",
                "configured_account_captured_at": "2026-07-30T04:00:00Z",
            }, ensure_ascii=False))
            discovery = {
                "status": "completed",
                "candidates": [{
                    "run_id": RUN_ID,
                    "discovery_source": "dynamic_search",
                    "aweme_id": "22345678901",
                    "source_url": "https://www.douyin.com/video/22345678901",
                    "title": "Dynamic AI",
                    "author": "creator",
                    "likes": 1000,
                    "comments": None,
                    "favorites": None,
                    "shares": None,
                    "published_at": "",
                    "published_at_display": "1天前",
                    "published_recency": {
                        "minimum_seconds": 86400,
                        "maximum_seconds": 172800,
                    },
                    "duration_seconds": 30,
                    "raw_identity": "raw",
                }],
                "source_ledger": [
                    {
                        "source": "recommendation",
                        "attempted": True,
                        "status": "completed_empty",
                        "reason": "",
                        "discovered_count": 0,
                    },
                    {
                        "source": "dynamic_search",
                        "attempted": True,
                        "status": "completed",
                        "reason": "",
                        "discovered_count": 1,
                    },
                ],
            }
            produced = {
                "packages": [{
                    "run_id": RUN_ID,
                    "aweme_id": aweme_id,
                    "source_url": source_url,
                    "status": "completed",
                    "caption_timeline": [{"text": title}],
                } for aweme_id, source_url, title in (
                    ("12345678901", url, "Configured AI angle"),
                    ("22345678901", "https://www.douyin.com/video/22345678901", "Dynamic AI"),
                )],
                "failures": [],
            }
            argv = [
                "run_daily_workflow.py",
                "--run-id", RUN_ID,
                "--business-date", "2026-07-30",
                "--workflow-db", str(root / "workflow.sqlite3"),
                "--artifact-root", str(root / "artifacts"),
                "--collection-fixture", str(fixture),
                "--video-mode", "normal",
            ]
            output = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch(
                    "run_daily_workflow.check_runtime_readiness",
                    return_value={"config_path": str(root / "runtime.json"),
                                  "policy_path": str(root / "policy.json")},
                ),
                mock.patch(
                    "run_daily_workflow.load_discovery_payload",
                    return_value=discovery,
                ) as load_discovery,
                mock.patch("run_daily_workflow.produce", return_value=produced),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(main(), 0)
            self.assertEqual(load_discovery.call_count, 1)
            result = json.loads(output.getvalue().strip().splitlines()[-1])
            self.assertEqual(result["action"], "editorial_required")
            self.assertEqual(result["candidate_count"], 2)
            handoff = json.loads(Path(result["handoff_path"]).read_text())
            self.assertEqual(len(handoff["candidates"]), 2)


if __name__ == "__main__":
    unittest.main()
