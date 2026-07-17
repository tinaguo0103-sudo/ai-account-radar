from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import content_ledger_reconcile as reconcile
import content_sampler


RUN_ID = "run_20260717_093104"


def item(index: int) -> content_sampler.ContentItem:
    if index < 87:
        source_type, platform = "对标视频", "抖音"
    elif index < 106:
        source_type, platform = "公众号文章", "微信"
    else:
        source_type, platform = "AIHOT热点", "AIHOT"
    return content_sampler.ContentItem(
        source_type=source_type, platform=platform, account_name=f"account-{index}", title=f"title-{index}",
        url=f"https://example.com/{index}", content_shape="article", cover_text="", body_snippet=f"body-{index}",
        published_at="2026-07-17", comment_questions="", ocr_text="", fetch_method="fixture",
        fetch_status="ok", failure_reason="", fingerprint=f"fp-{index:03d}",
    )


def record(value: str, run_id: str = RUN_ID, suffix: str = "", *, legacy: bool = False) -> dict:
    index = int(value.rsplit("-", 1)[-1])
    planned = item(index)
    return {
        "record_id": f"rec-{value}{suffix}",
        "fields": {
            "内容指纹": "" if legacy else value,
            "运行批次": run_id,
            "最近参与运行批次": run_id,
            "标题": planned.title,
            "来源类型": planned.source_type,
            "来源名称": planned.account_name,
            "作者/账号": planned.account_name,
            "平台": planned.platform,
            "链接": planned.url,
            "发布时间": planned.published_at,
        },
    }


class FakeLedger:
    def __init__(self, records: list[dict], *, behavior: str = "success") -> None:
        self.records = list(records)
        self.behavior = behavior
        self.calls = 0
        self.put_calls = 0
        self.post_calls = 0

    def read(self) -> list[dict]:
        return list(reversed(self.records))

    def create(self, fields: dict[str, str]) -> dict:
        self.calls += 1
        self.post_calls += 1
        value = fields["内容指纹"]
        if self.behavior == "hard_failure":
            raise RuntimeError("validation rejected")
        if self.behavior == "timeout_before_once" and self.calls == 1:
            raise TimeoutError("timed out before commit")
        self.records.append(record(value))
        if self.behavior == "timeout_after_once" and self.calls == 1:
            raise TimeoutError("timed out after commit")
        if self.behavior == "malformed_after_once" and self.calls == 1:
            return {"code": 0, "data": {}}
        return {"code": 0, "data": {"record": {"record_id": f"rec-{value}"}}}

    def update(self, record_id: str, fields: dict[str, str]) -> dict:
        self.calls += 1
        self.put_calls += 1
        target = next(row for row in self.records if row["record_id"] == record_id)
        if self.behavior == "hard_failure":
            raise RuntimeError("validation rejected")
        if self.behavior == "timeout_before_once" and self.calls == 1:
            raise TimeoutError("timed out before commit")
        target["fields"].update(fields)
        if self.behavior == "conflict_after_once" and self.calls == 1:
            target["fields"]["内容指纹"] = "conflicting-fingerprint"
        if self.behavior == "timeout_after_once" and self.calls == 1:
            raise TimeoutError("timed out after commit")
        if self.behavior == "malformed_after_once" and self.calls == 1:
            return {"code": 0, "data": {}}
        return {"code": 0, "data": {"record": {"record_id": record_id}}}


class AR034FContentLedgerReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [item(index) for index in range(162)]
        self.closure = {"ordered_canonical_fingerprints": [row.fingerprint for row in self.items[:87]]}
        self.exact = [record(row.fingerprint) for row in self.items[:136]]
        self.legacy = [record(row.fingerprint, legacy=True) for row in self.items[136:]]
        self.existing = [*self.exact, *self.legacy]

    def run_reconcile(self, store: FakeLedger, *, write: bool, validator=lambda: None, **kwargs):
        return reconcile.reconcile_missing_records(
            self.items, RUN_ID, self.closure, read_records=store.read, create_record=store.create,
            update_record=store.update, revalidate_plan=validator, write=write, sleep=lambda _: None, **kwargs,
        )

    def test_production_shape_check_only_reports_26_missing_without_writes(self) -> None:
        store = FakeLedger(self.existing)
        report = self.run_reconcile(store, write=False)
        self.assertTrue(report["ok"])
        self.assertEqual((report["planned"], report["existing"], report["missing"], report["writes"]), (162, 136, 26, 0))
        self.assertEqual((report["classification"]["legacy_update_count"], report["classification"]["create_count"]), (26, 0))
        self.assertEqual(report["classification"]["actual_duplicate_count"], 0)
        self.assertEqual(store.calls, 0)

    def test_first_reconcile_writes_only_26_then_full_162_and_87_pass(self) -> None:
        store = FakeLedger(self.existing)
        report = self.run_reconcile(store, write=True)
        self.assertTrue(report["ok"])
        self.assertEqual((store.put_calls, store.post_calls, report["legacy_updates"], report["created"]), (26, 0, 26, 0))
        self.assertEqual(report["classification"]["existing_unique_count"], 162)
        self.assertEqual(report["source_projection"]["source_projection_count"], 87)
        self.assertEqual(len(store.records), 162)

    def test_second_reconcile_is_noop(self) -> None:
        store = FakeLedger([record(row.fingerprint) for row in self.items])
        report = self.run_reconcile(store, write=True)
        self.assertTrue(report["ok"])
        self.assertEqual((report["missing"], report["writes"], store.calls), (0, 0, 0))

    def test_missing_is_not_duplicate(self) -> None:
        state = reconcile.classify_records(self.items, self.existing, RUN_ID)
        self.assertEqual(state["missing_count"], 26)
        self.assertEqual(state["actual_duplicate_count"], 0)
        self.assertEqual(state["duplicate_fingerprints"], [])
        with self.assertRaisesRegex(RuntimeError, "missing=26; wrong_run=0; duplicate=0"):
            content_sampler.verify_content_ledger_readback(self.items, self.existing, RUN_ID)

    def test_duplicate_and_wrong_run_fail_before_write(self) -> None:
        cases = {
            "duplicate": [*self.existing, record(self.items[0].fingerprint, suffix="-two")],
            "wrong_run": [record(self.items[0].fingerprint, "other"), *self.existing[1:]],
        }
        for name, records in cases.items():
            store = FakeLedger(records)
            with self.subTest(name=name), self.assertRaises(reconcile.ReconcileError):
                self.run_reconcile(store, write=True)
            self.assertEqual(store.calls, 0)

    def test_duplicate_or_empty_planned_input_fails(self) -> None:
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.validate_planned_items([], RUN_ID)
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.validate_planned_items([self.items[0], self.items[0]], RUN_ID)

    def test_timeout_after_commit_is_resolved_without_second_write(self) -> None:
        store = FakeLedger(self.existing, behavior="timeout_after_once")
        report = self.run_reconcile(store, write=True)
        self.assertTrue(report["ok"])
        self.assertEqual(store.calls, 26)
        self.assertEqual(report["already_committed"], 1)
        self.assertEqual(report["legacy_updates"], 25)

    def test_timeout_before_commit_retries_only_absent_fingerprint(self) -> None:
        store = FakeLedger(self.existing, behavior="timeout_before_once")
        report = self.run_reconcile(store, write=True)
        self.assertTrue(report["ok"])
        self.assertEqual(store.calls, 27)
        self.assertEqual(report["legacy_updates"], 26)

    def test_malformed_ambiguous_response_is_resolved_by_readback(self) -> None:
        store = FakeLedger(self.existing, behavior="malformed_after_once")
        report = self.run_reconcile(store, write=True)
        self.assertTrue(report["ok"])
        self.assertEqual(store.calls, 26)
        self.assertTrue(report["outcomes"][0]["ambiguity_resolved"])

    def test_post_update_conflict_stops_without_retry(self) -> None:
        store = FakeLedger(self.existing, behavior="conflict_after_once")
        with self.assertRaises(reconcile.ReconcileAbort) as raised:
            self.run_reconcile(store, write=True)
        self.assertEqual(store.put_calls, 1)
        self.assertEqual(store.post_calls, 0)
        self.assertIn("conflict", raised.exception.report["reason"])

    def test_rate_limit_after_commit_is_resolved_by_readback(self) -> None:
        store = FakeLedger(self.existing)
        original_update = store.update

        def rate_limited(record_id, fields):
            payload = original_update(record_id, fields)
            if store.calls == 1:
                raise RuntimeError("HTTP 429 rate limit; status unknown")
            return payload

        report = reconcile.reconcile_missing_records(
            self.items, RUN_ID, self.closure, read_records=store.read, create_record=store.create,
            update_record=rate_limited, revalidate_plan=lambda: None, write=True, sleep=lambda _: None,
        )
        self.assertTrue(report["ok"])
        self.assertEqual(store.calls, 26)
        self.assertEqual(report["already_committed"], 1)

    def test_one_item_hard_failure_stops_and_preserves_prior_success(self) -> None:
        store = FakeLedger(self.existing, behavior="hard_failure")
        report = self.run_reconcile(store, write=True)
        self.assertFalse(report["ok"])
        self.assertEqual((store.calls, report["failed"], len(store.records)), (1, 1, 162))

    def test_concurrent_insertion_is_already_committed(self) -> None:
        store = FakeLedger(self.existing)
        original_read = store.read
        reads = 0

        def concurrent_read():
            nonlocal reads
            reads += 1
            if reads == 2:
                target = next(row for row in store.records if row["record_id"] == f"rec-{self.items[136].fingerprint}")
                target["fields"]["内容指纹"] = self.items[136].fingerprint
            return original_read()

        report = reconcile.reconcile_missing_records(
            self.items, RUN_ID, self.closure, read_records=concurrent_read, create_record=store.create,
            update_record=store.update, revalidate_plan=lambda: None, write=True, sleep=lambda _: None,
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["already_committed"], 1)
        self.assertEqual(store.calls, 25)

    def test_plan_revalidation_failure_blocks_before_create(self) -> None:
        store = FakeLedger(self.existing)
        calls = 0

        def validator():
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise reconcile.ReconcileError("source_canonical_drift")

        with self.assertRaisesRegex(reconcile.ReconcileError, "source_canonical_drift"):
            self.run_reconcile(store, write=True, validator=validator)
        self.assertEqual(store.calls, 0)

    def test_plan_drift_after_one_commit_preserves_accurate_failure_report(self) -> None:
        store = FakeLedger(self.existing)
        validations = 0

        def validator():
            nonlocal validations
            validations += 1
            if validations >= 4:
                raise reconcile.ReconcileError("source_canonical_drift")

        with self.assertRaises(reconcile.ReconcileAbort) as raised:
            self.run_reconcile(store, write=True, validator=validator)
        report = raised.exception.report
        self.assertFalse(report["ok"])
        self.assertEqual((store.calls, report["writes"]), (1, 1))
        self.assertEqual(report["side_effect_stage"], "plan_revalidation_failed")

    def test_post_write_source_projection_reorder_fails(self) -> None:
        store = FakeLedger([record(row.fingerprint) for row in self.items])
        reordered = list(self.items)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        with self.assertRaisesRegex(Exception, "feishu_03_readback_identity_mismatch"):
            reconcile.reconcile_missing_records(
                reordered, RUN_ID, self.closure, read_records=store.read, create_record=store.create,
                update_record=store.update, revalidate_plan=lambda: None, write=True, sleep=lambda _: None,
            )

    def test_true_absence_creates_only_absent_item(self) -> None:
        store = FakeLedger(self.existing[:-1])
        report = self.run_reconcile(store, write=True)
        self.assertTrue(report["ok"])
        self.assertEqual((store.put_calls, store.post_calls, report["legacy_updates"], report["created"]), (25, 1, 25, 1))
        self.assertEqual(len(store.records), 162)

    def test_legacy_ambiguity_and_identity_drift_fail_before_mutation(self) -> None:
        mutations = []
        mutations.append([*self.existing, record(self.items[136].fingerprint, suffix="-two", legacy=True)])
        for field, value in [
            ("链接", "https://wrong.example/item"),
            ("标题", "wrong title"),
            ("来源类型", "wrong source"),
            ("作者/账号", "wrong account"),
            ("平台", "wrong platform"),
            ("发布时间", "2020-01-01"),
        ]:
            rows = [dict(row, fields=dict(row["fields"])) for row in self.existing]
            rows[136]["fields"][field] = value
            mutations.append(rows)
        missing_id = [dict(row, fields=dict(row["fields"])) for row in self.existing]
        missing_id[136]["record_id"] = ""
        mutations.append(missing_id)
        conflicting = [dict(row, fields=dict(row["fields"])) for row in self.existing]
        conflicting[136]["fields"]["内容指纹"] = "fp-conflict"
        mutations.append(conflicting)
        for rows in mutations:
            store = FakeLedger(rows)
            with self.subTest(), self.assertRaises(reconcile.ReconcileError):
                self.run_reconcile(store, write=True)
            self.assertEqual(store.calls, 0)

    def test_future_writer_short_legacy_record_gets_fingerprint(self) -> None:
        planned = self.items[136]
        existing = [record(planned.fingerprint, legacy=True)]
        with mock.patch.multiple(
            content_sampler,
            require_feishu_env=mock.DEFAULT,
            list_tables=mock.DEFAULT,
            ensure_content_inbox_fields=mock.DEFAULT,
            all_records=mock.DEFAULT,
            update_record_fields=mock.DEFAULT,
            batch_create_records=mock.DEFAULT,
            ensure_content_inbox_today_view=mock.DEFAULT,
        ) as patched, mock.patch.object(content_sampler.feishu, "tenant_token", return_value="token"):
            patched["require_feishu_env"].return_value = "app"
            patched["list_tables"].return_value = {"内容库": "table", content_sampler.table_name("content_inbox"): "table"}
            patched["ensure_content_inbox_fields"].return_value = []
            patched["all_records"].side_effect = [existing, [record(planned.fingerprint)]]
            patched["batch_create_records"].return_value = 0
            patched["ensure_content_inbox_today_view"].return_value = {}
            result = content_sampler.write_content_ledger_to_feishu([planned], RUN_ID)
        update_fields = patched["update_record_fields"].call_args.args[-1]
        self.assertEqual(update_fields["内容指纹"], planned.fingerprint)
        patched["batch_create_records"].assert_not_called()
        self.assertEqual(result["created_records"], 0)

    def test_future_writer_conflicting_fingerprint_fails_without_mutation(self) -> None:
        planned = self.items[136]
        conflicting = record(planned.fingerprint, legacy=True)
        conflicting["fields"]["内容指纹"] = "other-fingerprint"
        with mock.patch.multiple(
            content_sampler,
            require_feishu_env=mock.DEFAULT,
            list_tables=mock.DEFAULT,
            ensure_content_inbox_fields=mock.DEFAULT,
            all_records=mock.DEFAULT,
            update_record_fields=mock.DEFAULT,
            batch_create_records=mock.DEFAULT,
        ) as patched, mock.patch.object(content_sampler.feishu, "tenant_token", return_value="token"):
            patched["require_feishu_env"].return_value = "app"
            patched["list_tables"].return_value = {content_sampler.table_name("content_inbox"): "table"}
            patched["ensure_content_inbox_fields"].return_value = []
            patched["all_records"].return_value = [conflicting]
            with self.assertRaisesRegex(RuntimeError, "conflicting_fingerprint"):
                content_sampler.write_content_ledger_to_feishu([planned], RUN_ID)
        patched["update_record_fields"].assert_not_called()
        patched["batch_create_records"].assert_not_called()

    def test_schema_and_artifact_hash_drift_fail(self) -> None:
        with mock.patch.object(content_sampler, "fields_by_name", return_value={"\u5185\u5bb9\u6307\u7eb9": {}, "\u8fd0\u884c\u6279\u6b21": {}}):
            with self.assertRaises(reconcile.ReconcileError):
                reconcile.validate_schema("token", "app", "table")
        wrong_type = {name: {"type": 1} for name in reconcile.REQUIRED_IDENTITY_FIELDS}
        wrong_type["内容指纹"] = {"type": 4}
        with mock.patch.object(content_sampler, "fields_by_name", return_value=wrong_type):
            with self.assertRaisesRegex(reconcile.ReconcileError, "field_type_mismatch"):
                reconcile.validate_schema("token", "app", "table")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.csv"; path.write_text("x", encoding="utf-8")
            self.assertNotEqual(reconcile.file_sha256(path), "0" * 64)

    def test_all_records_pagination_preserves_complete_identity_set(self) -> None:
        pages = [
            {"data": {"items": self.existing[:100], "has_more": True, "page_token": "next"}},
            {"data": {"items": self.existing[100:], "has_more": False}},
        ]
        with mock.patch.object(content_sampler.feishu, "request_json", side_effect=pages) as request:
            records = content_sampler.all_records("token", "app", "table")
        self.assertEqual(len(records), 162)
        self.assertEqual(request.call_count, 2)

    def test_recovery_module_never_calls_full_writer(self) -> None:
        source = Path(reconcile.__file__).read_text(encoding="utf-8")
        self.assertNotIn("write_content_ledger_to_feishu(", source)
        with mock.patch.object(content_sampler, "write_content_ledger_to_feishu") as writer:
            self.run_reconcile(FakeLedger(self.existing), write=False)
            writer.assert_not_called()

    def test_atomic_report_is_complete_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            reconcile.atomic_write_json(path, {"ok": True, "planned": 162})
            self.assertEqual(json.loads(path.read_text()), {"ok": True, "planned": 162})


if __name__ == "__main__":
    unittest.main()
