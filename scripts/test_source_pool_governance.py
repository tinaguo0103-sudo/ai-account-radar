#!/usr/bin/env python3
from __future__ import annotations

import unittest

import topic_flow_rework as flow


def source(name: str, *, role: str = "current_aux_competitor", enabled: bool = True) -> dict:
    return {
        "account_name": name,
        "source_role": role,
        "source_group": role,
        "platform": "抖音",
        "url": f"https://example.com/{name}",
        "default_enabled": enabled,
        "participates_main_sampling": enabled,
    }


class SourcePoolGovernanceTests(unittest.TestCase):
    def test_polluted_sources_are_quarantined_in_dry_run_plan(self) -> None:
        plan = flow.source_governance_plan([
            source("Austin适配AI账号"),
            source("琼玩车"),
            source("AI短视频工坊"),
        ])

        self.assertEqual(plan["active_competitor_count"], 1)
        self.assertEqual(plan["polluted_match_count"], 2)
        self.assertEqual(
            {action["name"] for action in plan["dry_run_actions"]},
            {"琼玩车", "AI短视频工坊"},
        )
        for action in plan["dry_run_actions"]:
            self.assertFalse(action["would_set"]["default_enabled"])
            self.assertFalse(action["would_set"]["participates_main_sampling"])

    def test_coverage_report_keeps_full_account_pool_without_default_twelve_cap(self) -> None:
        sources = [source(f"AI账号{i:02d}") for i in range(14)] + [source("UDG终极梦想车库")]
        probe_rows = [
            {"account_name": "AI账号00", "status": "success", "resolved_items": 2},
            {"account_name": "AI账号01", "status": "failed", "failure_reason": "needs_login", "resolved_items": 0},
        ]

        report = flow.collection_coverage_report(sources, probe_rows)

        self.assertEqual(report["planned_account_count"], 14)
        self.assertGreater(report["planned_account_count"], 12)
        self.assertEqual(report["attempted_account_count"], 2)
        self.assertEqual(report["successful_account_count"], 1)
        self.assertEqual(report["polluted_sources"], ["UDG终极梦想车库"])
        self.assertFalse(report["future_batching"]["enabled_now"])

    def test_csv_probe_stringified_video_links_count_as_items_not_characters(self) -> None:
        sources = [source("AI账号00")]
        probe_rows = [{
            "account_name": "AI账号00",
            "status": "success",
            "video_links": "[\"https://example.com/a\", \"https://example.com/b\"]",
        }]

        report = flow.collection_coverage_report(sources, probe_rows)

        self.assertEqual(report["per_account_artifact_counts"]["AI账号00"], 2)


if __name__ == "__main__":
    unittest.main()
