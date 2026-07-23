#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest import mock

import canonical_owner_projection as owners
import content_sampler
import daily_pipeline
import persona_counterfactual_audit as persona_audit
import refresh_console_daily
import topic_editorial_state_machine as editorial


RUN_ID = "run_20260718_092635"


def item(fingerprint: str, url: str, title: str = "title") -> content_sampler.ContentItem:
    return content_sampler.ContentItem(
        source_type="AIHOT热点",
        platform="AIHOT",
        account_name="AIHOT",
        title=title,
        url=url,
        content_shape="article",
        cover_text="",
        body_snippet="body",
        published_at="2026-07-18",
        comment_questions="",
        ocr_text="",
        fetch_method="fixture",
        fetch_status="success",
        failure_reason="",
        fingerprint=fingerprint,
    )


def record(record_id: str, fingerprint: str, url: str, run_id: str) -> dict:
    return {
        "record_id": record_id,
        "fields": {
            "内容指纹": fingerprint,
            "链接": url,
            "运行批次": run_id,
            "最近参与运行批次": run_id,
            "标题": "title",
            "来源类型": "AIHOT热点",
            "来源名称": "AIHOT",
            "作者/账号": "AIHOT",
            "平台": "AIHOT",
            "发布时间": "2026-07-18",
        },
    }


class BusinessContinuityTests(unittest.TestCase):
    def douyin_probe(self) -> dict:
        return {
            "run_id": RUN_ID,
            "status": "completed_with_failures",
            "item_lineage": {"ok": True},
            "coverage": {
                "invariants": {
                    "attempted_equals_planned": True,
                    "success_plus_failed_equals_attempted": True,
                    "account_lineage_unique_and_complete": True,
                },
                "failed_accounts": [
                    {
                        "account_name": "failed-account",
                        "status": "partial_untrusted",
                        "failure_reason": "isolated",
                        "artifact_count": 0,
                    }
                ],
                "per_account_artifact_counts": {
                    "successful-account": 3,
                    "failed-account": 0,
                },
            },
        }

    def test_source_local_failures_leave_truthful_downstream_usable(self) -> None:
        wechat = {"name": "wewe refresh", "returncode": 3, "stderr": "login_required"}
        daily_pipeline.isolate_source_failure(
            wechat, source="wechat", state="login_required", reason="login_required"
        )
        steps = [
            wechat,
            {"name": "start/verify canonical Douyin Chrome CDP", "returncode": 0},
            {"name": "verify canonical Douyin profile login session", "returncode": 0},
            {
                "name": "fetch daily Douyin homepage title/caption samples through Chrome CDP",
                "returncode": 0,
                "optional_failed": True,
            },
        ]
        closure = {
            "run_id": RUN_ID,
            "manual_artifact_identity_verified": True,
            "combined_sha256": "combined",
            "content_items_sha256": "content",
            "comparison_universe_count": 2,
            "feishu_03_identity": {
                "ok": True,
                "planned_identity": {"identity_sha256": "planned"},
            },
        }
        with mock.patch.object(daily_pipeline, "read_json", return_value=self.douyin_probe()):
            report = daily_pipeline.downstream_usability_report(
                steps, daily_pipeline.OUT / "runs" / RUN_ID, 1, ingestion_closure=closure
            )
        self.assertFalse(report["full_collection_success"])
        self.assertEqual(report["collection_status"], "completed_with_failures")
        self.assertTrue(report["downstream_usable"])
        self.assertEqual(report["system_failure_count"], 0)
        self.assertEqual(report["isolated_source_failures"][0]["source"], "wechat")

    def test_historical_owner_is_reused_while_safe_and_new_continue(self) -> None:
        planned = [
            item("planned-history", "https://example.com/history"),
            item("current-owner", "https://example.com/current"),
            item("genuinely-new", "https://example.com/new"),
        ]
        existing = [
            record("rec-history", "history-owner", "https://example.com/history", "run_20260717_000000"),
            record("rec-current", "current-owner", "https://example.com/current", RUN_ID),
        ]
        projection = owners.resolve_owner_projection(planned, existing, RUN_ID, allow_new=True)
        manifest = projection.manifest
        self.assertEqual([row.fingerprint for row in projection.projected_items], ["history-owner", "current-owner", "genuinely-new"])
        self.assertEqual(manifest["safe_count"], 3)
        self.assertEqual(manifest["created_count"], 1)
        self.assertEqual(manifest["historical_participation_count"], 1)
        self.assertEqual(
            manifest["mappings"][0]["resolution"], "existing_historical"
        )

    def test_current_run_owner_ambiguity_remains_blocking(self) -> None:
        planned = [item("planned", "https://example.com/same")]
        existing = [
            record("rec-1", "owner-1", "https://example.com/same", RUN_ID),
            record("rec-2", "owner-2", "https://example.com/same", RUN_ID),
        ]
        with self.assertRaisesRegex(owners.OwnerProjectionError, "canonical_owner_ambiguous"):
            owners.resolve_owner_projection(planned, existing, RUN_ID, allow_new=True)

    def test_candidate_failure_has_zero_replacement_and_survivor_continues(self) -> None:
        state = {
            "stages": {
                "research": {
                    "candidates": {
                        "failed": {"status": "failed"},
                        "survivor": {"status": "completed"},
                    }
                }
            }
        }
        self.assertEqual(editorial.completed_candidate_ids(state, "research"), {"survivor"})

    def test_small_n_is_not_applicable_and_four_rows_keep_threshold(self) -> None:
        for count in (1, 2, 3):
            rows = [{"今日建议级别": "推荐制作", "选题命题": "为什么现在要做"}] * count
            report = persona_audit.actionable_title_family_report(rows)
            self.assertTrue(report["ok"])
            self.assertEqual(report["classification"], "not_applicable_small_n")
        diverse = [
            {"今日建议级别": "推荐制作", "选题命题": "不是工具而是流程"},
            {"今日建议级别": "推荐制作", "选题命题": "为什么现在要做"},
            {"今日建议级别": "推荐制作", "选题命题": "一条视频突然出圈"},
            {"今日建议级别": "推荐制作", "选题命题": "上线之后结果已经改变"},
        ]
        self.assertTrue(persona_audit.actionable_title_family_report(diverse)["ok"])
        dominated = [{"今日建议级别": "推荐制作", "选题命题": "为什么现在要做"}] * 4
        self.assertFalse(persona_audit.actionable_title_family_report(dominated)["ok"])

    def test_optional_console_failure_does_not_reverse_finalization(self) -> None:
        steps = [
            {"name": "write Feishu 04", "returncode": 0},
            {"name": "verify Feishu 04", "returncode": 0},
            {
                "name": "refresh Feishu 00",
                "returncode": 1,
                "optional_followup_failed": True,
            },
        ]
        self.assertTrue(daily_pipeline.business_steps_ok(steps))
        tables = {
            "00 主控台": "tbl-console",
            "03 内容收件箱": "tbl-content",
            "04 分析与选题": "tbl-topic",
            "06 完整脚本与制作包": "tbl-script",
            "07 资产与复盘": "tbl-assets",
            "08 学习记录": None,
        }
        self.assertEqual(refresh_console_daily.missing_table_report(tables), {
            "required": [],
            "optional": ["08 学习记录"],
        })

    def test_integrated_partial_flow_reaches_safe_plan_survivor_and_finalization(self) -> None:
        self.test_source_local_failures_leave_truthful_downstream_usable()
        self.test_historical_owner_is_reused_while_safe_and_new_continue()
        self.test_candidate_failure_has_zero_replacement_and_survivor_continues()
        self.test_optional_console_failure_does_not_reverse_finalization()


if __name__ == "__main__":
    unittest.main()
