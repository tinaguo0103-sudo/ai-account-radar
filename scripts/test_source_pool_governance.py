#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest import mock

import topic_flow_rework as flow
import source_pool_governance as governance
import run_daily_collection_job


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

    def test_get_only_migration_plan_locks_exact_eight_and_other_records(self) -> None:
        polluted = sorted(flow.POLLUTED_SOURCE_NAMES)
        records = [
            {"record_id": f"polluted-{index}", "fields": {
                "名称": name,
                "来源角色": "current_aux_competitor",
                "默认启用": "启用",
                "是否参与主采样": "是",
                "优先级": "medium",
            }}
            for index, name in enumerate(polluted)
        ]
        records.extend({
            "record_id": f"valid-{index}",
            "fields": {"名称": f"valid-{index}", "来源角色": "current_aux_competitor"},
        } for index in range(43))

        plan = governance.migration_plan(records)

        self.assertTrue(plan["ok"])
        self.assertEqual(plan["record_count"], 51)
        self.assertEqual(plan["target_count"], 8)
        self.assertEqual(plan["untouched_count"], 43)
        self.assertEqual({row["fields"]["来源角色"] for row in plan["planned_mutations"]}, {"quarantined_source"})
        self.assertFalse(plan["writes_feishu"])

    def test_migration_plan_fails_closed_for_missing_or_duplicate_polluted_row(self) -> None:
        names = sorted(flow.POLLUTED_SOURCE_NAMES)
        records = [
            {"record_id": str(index), "fields": {"名称": name}}
            for index, name in enumerate(names[:-1] + [names[0]])
        ]
        records.extend({"record_id": f"x-{index}", "fields": {"名称": f"valid-{index}"}} for index in range(43))

        plan = governance.migration_plan(records)

        self.assertFalse(plan["ok"])
        self.assertTrue(plan["missing_polluted_names"])
        self.assertTrue(plan["duplicate_polluted_names"])

    def test_source_identity_does_not_fall_back_to_legacy_name_or_link(self) -> None:
        row = governance.source_from_feishu_record({
            "record_id": "rec-1",
            "fields": {"来源名称": "legacy", "链接": "https://legacy.invalid"},
        })
        self.assertEqual(row["account_name"], "")
        self.assertEqual(row["url"], "")

    def test_historical_content_audit_is_read_only_and_hash_bound(self) -> None:
        records = [
            {"record_id": "a", "fields": {"原始来源账号": "琼玩车"}},
            {"record_id": "b", "fields": {"原始来源账号": "valid"}},
        ]
        audit = governance.historical_content_audit(records)
        self.assertEqual(audit["record_count"], 2)
        self.assertEqual(audit["polluted_name_historical_match_count"], 1)
        self.assertFalse(audit["writes_feishu"])
        self.assertFalse(audit["touches_historical_03"])

    def test_feishu_loader_uses_get_only_helpers(self) -> None:
        with mock.patch.object(governance, "load_local_env"), \
             mock.patch.object(governance.os, "getenv", return_value="app"), \
             mock.patch.object(governance.feishu, "tenant_token", return_value="token"), \
             mock.patch.object(governance, "list_tables", return_value={
                 "01 来源与采样": "source-table", "03 内容收件箱": "content-table",
             }), \
             mock.patch.object(governance, "list_fields", return_value={}), \
             mock.patch.object(governance, "all_records", return_value=[]):
            context = governance.load_feishu_context()
        self.assertEqual(context["source_table_id"], "source-table")
        self.assertEqual(context["content_table_id"], "content-table")

    def test_scheduled_check_only_reports_all_platforms_without_twelve_cap(self) -> None:
        sources = [source(f"douyin-{index}") for index in range(31)]
        sources.extend([
            {**source("wechat"), "platform": "微信公众号"},
            {**source("mixed"), "platform": "公众号/X/AIHOT"},
        ])
        with mock.patch.object(run_daily_collection_job.flow, "load_json_config", return_value={"sources": sources}):
            plan = run_daily_collection_job.scheduled_collection_plan(governance.CONFIG, 0)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["planned_accounts"], 33)
        self.assertEqual(plan["planned_douyin_accounts"], 31)
        self.assertEqual(plan["planned_other_accounts"], 2)
        self.assertEqual(plan["douyin_account_limit"], 0)
        self.assertFalse(plan["writes_feishu"])

    def test_scheduled_check_only_rejects_any_positive_account_cap(self) -> None:
        with mock.patch.object(run_daily_collection_job.flow, "load_json_config", return_value={"sources": [source("one")]}):
            plan = run_daily_collection_job.scheduled_collection_plan(governance.CONFIG, 12)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["status"], "limited_plan_rejected")

    def test_active_scheduled_path_has_no_collection_fallback(self) -> None:
        audit = governance.zero_fallback_audit()
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["prohibited_path_count"], 0)


if __name__ == "__main__":
    unittest.main()
