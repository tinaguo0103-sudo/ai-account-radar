#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import feishu_topic_decision_card as card


def record(
    record_id: str,
    *,
    title: str,
    run_id: str,
    day: str,
    rank: str = "1",
    status: str = "待判断",
    source: str = "",
    generated: str = "",
    action: str = "生成脚本包",
    title_permission: str = "可发布标题",
    level: str = "今日最值得做",
) -> dict:
    return {
        "record_id": record_id,
        "fields": {
            "选题标题": title,
            "原始来源标题": source or title,
            "运行批次": run_id,
            "推荐日期": day,
            "今日排名": rank,
            "状态": status,
            "是否已生成脚本稿": generated,
            "今日建议级别": level,
            "推荐动作": action,
            "title_permission": title_permission,
            "可发布标题": title,
            "AI味风险": "低",
            "一句话Brief": f"{title} brief",
            "我要做的实验": f"{title} experiment",
            "对应方向": "AI工作流",
            "需要补的证据": "需要补一张截图" if action == "补证据" else "",
        },
    }


class AR013CompensationPoolTest(unittest.TestCase):
    def fetch_with_records(self, records: list[dict], *, sent: set[str] | None = None, limit: int = 7) -> list[dict]:
        with patch.object(card.feishu, "tenant_token", return_value="token"), \
                patch.object(card, "require_app_token", return_value="app"), \
                patch.object(card, "get_topic_table", return_value="table"), \
                patch.object(card, "all_records", return_value=records), \
                patch.object(card, "load_card_candidate_ledger", return_value=sent or set()), \
                patch.object(card, "compensation_pool_window", return_value=(date(2026, 7, 2), date(2026, 7, 4))):
            _token, _app, _table, selected = card.fetch_candidates("run_20260704_080730", limit)
        return selected

    def test_fetch_candidates_includes_today_and_recent_unsent_without_bonus_quota(self) -> None:
        selected = self.fetch_with_records([
            record("rec_today", title="Today", run_id="run_20260704_080730", day="2026-07-04", rank="3"),
            record("rec_old1", title="Old 1", run_id="run_20260703_080000", day="2026-07-03", rank="1"),
            record("rec_old2", title="Old 2", run_id="run_20260702_080000", day="2026-07-02", rank="2"),
            record("rec_too_old", title="Too old", run_id="run_20260701_080000", day="2026-07-01", rank="1"),
        ])

        self.assertEqual([item["record_id"] for item in selected], ["rec_old1", "rec_old2", "rec_today"])

    def test_fetch_candidates_excludes_processed_sent_and_generated_records(self) -> None:
        selected = self.fetch_with_records(
            [
                record("rec_ok", title="OK", run_id="run_20260704_080730", day="2026-07-04"),
                record("rec_done", title="Done", run_id="run_20260703_080000", day="2026-07-03", status="生成脚本包"),
                record("rec_no", title="No", run_id="run_20260703_080000", day="2026-07-03", status="不做"),
                record("rec_generated", title="Generated", run_id="run_20260703_080000", day="2026-07-03", generated="是"),
                record("rec_sent", title="Sent", run_id="run_20260703_080000", day="2026-07-03"),
            ],
            sent={"rec_sent"},
        )

        self.assertEqual([item["record_id"] for item in selected], ["rec_ok"])

    def test_fetch_candidates_deduplicates_history_against_today(self) -> None:
        selected = self.fetch_with_records([
            record("rec_old", title="Same Topic", run_id="run_20260703_080000", day="2026-07-03", rank="1", source="same-source"),
            record("rec_today", title="Same Topic", run_id="run_20260704_080730", day="2026-07-04", rank="7", source="same-source"),
        ])

        self.assertEqual([item["record_id"] for item in selected], ["rec_today"])

    def test_fetch_candidates_preserves_existing_global_limit(self) -> None:
        selected = self.fetch_with_records([
            record("rec1", title="One", run_id="run_20260703_080000", day="2026-07-03", rank="1"),
            record("rec2", title="Two", run_id="run_20260703_080000", day="2026-07-03", rank="2"),
            record("rec3", title="Three", run_id="run_20260704_080730", day="2026-07-04", rank="3"),
        ], limit=2)

        self.assertEqual(card.DEFAULT_LIMIT, 7)
        self.assertEqual([item["record_id"] for item in selected], ["rec1", "rec2"])

    def test_build_card_shows_coverage_dates_original_date_and_run_id(self) -> None:
        built = card.build_card([
            record("rec_old", title="Old", run_id="run_20260703_080000", day="2026-07-03"),
            record("rec_today", title="Today", run_id="run_20260704_080730", day="2026-07-04"),
        ], "run_20260704_080730")
        text = str(built)
        value = card.card_candidate_value(built)

        self.assertEqual(value["coverage_dates"], ["2026-07-03", "2026-07-04"])
        self.assertIn("本次候选覆盖：2026-07-03、2026-07-04", text)
        self.assertIn("run：run_20260703_080000", text)
        self.assertEqual(value["candidate_snapshots"]["rec_old"]["run_id"], "run_20260703_080000")
        self.assertEqual(value["candidate_snapshots"]["rec_old"]["date"], "2026-07-03")

    def test_build_card_keeps_evidence_candidates_out_of_script_package_selector(self) -> None:
        built = card.build_card([
            record("rec_script", title="Script", run_id="run_20260704_080730", day="2026-07-04"),
            record("rec_evidence", title="Evidence", run_id="run_20260704_080730", day="2026-07-04", action="补证据", level="可选候选", title_permission="内部测试标题"),
        ], "run_20260704_080730")
        value = card.card_candidate_value(built)
        text = str(built)

        self.assertEqual(value["candidate_ids"], ["rec_script"])
        self.assertEqual(value["display_candidate_ids"], ["rec_script", "rec_evidence"])
        self.assertEqual(value["supplement_candidate_ids"], ["rec_evidence"])
        self.assertIn("补证据/观察候选：1 条", text)
        self.assertIn("不会进入下方“生成脚本包”勾选列表", text)
        self.assertIn("只显示可直接进入 06 的编号", text)

    def test_write_card_candidate_ledger_marks_all_displayed_candidates_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "candidate_ledger.jsonl"
            with patch.object(card, "CANDIDATE_LEDGER", ledger):
                built = card.build_card([
                    record("rec_script", title="Script", run_id="run_20260704_080730", day="2026-07-04"),
                    record("rec_evidence", title="Evidence", run_id="run_20260704_080730", day="2026-07-04", action="补证据", level="可选候选"),
                ], "run_20260704_080730")
                card.write_card_candidate_ledger(built, "run_20260704_080730", "/tmp/preview.json")
            text = ledger.read_text(encoding="utf-8")

        self.assertIn("rec_script", text)
        self.assertIn("rec_evidence", text)

    def test_apply_form_value_allows_historical_candidate_when_card_snapshot_matches(self) -> None:
        records = [
            record("rec_old", title="Old", run_id="run_20260703_080000", day="2026-07-03"),
            record("rec_today", title="Today", run_id="run_20260704_080730", day="2026-07-04"),
        ]
        with patch.object(card, "all_records", return_value=records):
            summary = card.apply_form_value(
                "token",
                "app",
                "table",
                {card.ENTER_SCRIPT_PACKAGE_FORM_KEY: ["rec_old"]},
                candidate_ids=["rec_old", "rec_today"],
                candidate_snapshots={"rec_old": {"run_id": "run_20260703_080000"}, "rec_today": {"run_id": "run_20260704_080730"}},
                run_id="run_20260704_080730",
                write=False,
            )

        self.assertEqual(summary["candidate_update_count"], 2)
        self.assertEqual(summary["skipped"], [])
        self.assertEqual(summary["updates"][0]["record_id"], "rec_old")
        self.assertEqual(summary["updates"][0]["fields"]["选择原因标签"], "")

    def test_apply_form_value_formats_reason_tags_for_text_fields(self) -> None:
        records = [record("rec_a", title="A", run_id="run_20260704_080730", day="2026-07-04")]
        with patch.object(card, "all_records", return_value=records):
            summary = card.apply_form_value(
                "token",
                "app",
                "table",
                {
                    card.ENTER_SCRIPT_PACKAGE_FORM_KEY: ["rec_a"],
                    "positive_reason_tags": ["证据够", "判断够强"],
                },
                candidate_ids=["rec_a"],
                run_id="run_20260704_080730",
                write=False,
            )

        self.assertEqual(summary["updates"][0]["fields"]["选择原因标签"], "证据够、判断够强")

    def test_apply_form_value_preserves_reason_tags_for_multi_select_fields(self) -> None:
        records = [record("rec_a", title="A", run_id="run_20260704_080730", day="2026-07-04")]
        records[0]["fields"]["选择原因标签"] = []
        with patch.object(card, "all_records", return_value=records):
            summary = card.apply_form_value(
                "token",
                "app",
                "table",
                {
                    card.ENTER_SCRIPT_PACKAGE_FORM_KEY: ["rec_a"],
                    "positive_reason_tags": ["证据够"],
                },
                candidate_ids=["rec_a"],
                run_id="run_20260704_080730",
                write=False,
            )

        self.assertEqual(summary["updates"][0]["fields"]["选择原因标签"], ["证据够"])

    def test_apply_form_value_rejects_cross_run_candidate_without_snapshot(self) -> None:
        records = [record("rec_old", title="Old", run_id="run_20260703_080000", day="2026-07-03")]
        with patch.object(card, "all_records", return_value=records):
            summary = card.apply_form_value(
                "token",
                "app",
                "table",
                {card.ENTER_SCRIPT_PACKAGE_FORM_KEY: ["rec_old"]},
                candidate_ids=["rec_old"],
                run_id="run_20260704_080730",
                write=False,
            )

        self.assertEqual(summary["candidate_update_count"], 0)
        self.assertEqual(summary["skipped"][0]["reason"], "run_id_mismatch")

    def test_candidate_ledger_records_only_ids_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "candidate_ledger.jsonl"
            with patch.object(card, "CANDIDATE_LEDGER", ledger):
                built = card.build_card([
                    record("rec1", title="Private Topic Body", run_id="run_20260704_080730", day="2026-07-04")
                ], "run_20260704_080730")
                card.write_card_candidate_ledger(built, "run_20260704_080730", "/tmp/preview.json")
            text = ledger.read_text(encoding="utf-8")

        self.assertIn("rec1", text)
        self.assertIn("title_hash", text)
        self.assertNotIn("Private Topic Body", text)


class TopicCardTableIsolationTest(unittest.TestCase):
    def test_get_topic_table_prefers_explicit_staging_table_id(self) -> None:
        with patch.dict("os.environ", {"FEISHU_TOPIC_TABLE_ID": "tbl_staging_topic"}), \
                patch.object(card.feishu, "list_tables", side_effect=AssertionError("list_tables should not be called")):
            table_id = card.get_topic_table("token", "app")

        self.assertEqual(table_id, "tbl_staging_topic")

    def test_get_topic_table_falls_back_to_table_name_without_explicit_id(self) -> None:
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(card.feishu, "list_tables", return_value=[
                    {"name": "04 分析与选题", "table_id": "tbl_by_name"},
                ]):
            table_id = card.get_topic_table("token", "app")

        self.assertEqual(table_id, "tbl_by_name")

    def test_fetch_candidates_uses_explicit_table_id_for_build_path(self) -> None:
        captured: dict[str, str] = {}

        def fake_all_records(_token: str, _app_token: str, table_id: str) -> list[dict]:
            captured["table_id"] = table_id
            return [
                record("rec_staging", title="Staging", run_id="run_staging", day="2026-07-04"),
            ]

        with patch.dict("os.environ", {"FEISHU_TOPIC_DECISION_TABLE_ID": "tbl_staging_topic"}), \
                patch.object(card.feishu, "tenant_token", return_value="token"), \
                patch.object(card, "require_app_token", return_value="app"), \
                patch.object(card.feishu, "list_tables", side_effect=AssertionError("list_tables should not be called")), \
                patch.object(card, "all_records", side_effect=fake_all_records), \
                patch.object(card, "load_card_candidate_ledger", return_value=set()), \
                patch.object(card, "compensation_pool_window", return_value=(date(2026, 7, 2), date(2026, 7, 4))):
            _token, _app, table_id, selected = card.fetch_candidates("run_staging", 7)

        self.assertEqual(table_id, "tbl_staging_topic")
        self.assertEqual(captured["table_id"], "tbl_staging_topic")
        self.assertEqual([item["record_id"] for item in selected], ["rec_staging"])

    def test_fetch_candidates_strict_run_id_excludes_compensation_pool_records(self) -> None:
        records = [
            record("rec_today", title="Today", run_id="run_20260704_080730", day="2026-07-04", rank="2"),
            record("rec_old", title="Old probe", run_id="ar018_target_test_20260705_120336", day="2026-07-05", rank="1"),
        ]
        with patch.object(card.feishu, "tenant_token", return_value="token"), \
                patch.object(card, "require_app_token", return_value="app"), \
                patch.object(card, "get_topic_table", return_value="table"), \
                patch.object(card, "all_records", return_value=records), \
                patch.object(card, "load_card_candidate_ledger", return_value=set()), \
                patch.object(card, "compensation_pool_window", return_value=(date(2026, 7, 2), date(2026, 7, 7))):
            _token, _app, _table, selected = card.fetch_candidates_for_card("run_20260704_080730", 7, strict_run_id=True)

        self.assertEqual([item["record_id"] for item in selected], ["rec_today"])

    def test_fetch_candidates_record_id_filter_limits_l3_probe(self) -> None:
        records = [
            record("rec_a", title="A", run_id="run_20260704_080730", day="2026-07-04", rank="1"),
            record("rec_b", title="B", run_id="run_20260704_080730", day="2026-07-04", rank="2"),
        ]
        with patch.object(card.feishu, "tenant_token", return_value="token"), \
                patch.object(card, "require_app_token", return_value="app"), \
                patch.object(card, "get_topic_table", return_value="table"), \
                patch.object(card, "all_records", return_value=records), \
                patch.object(card, "load_card_candidate_ledger", return_value=set()), \
                patch.object(card, "compensation_pool_window", return_value=(date(2026, 7, 2), date(2026, 7, 7))):
            _token, _app, _table, selected = card.fetch_candidates_for_card("run_20260704_080730", 7, strict_run_id=True, record_ids={"rec_b"})

        self.assertEqual([item["record_id"] for item in selected], ["rec_b"])


if __name__ == "__main__":
    unittest.main()
