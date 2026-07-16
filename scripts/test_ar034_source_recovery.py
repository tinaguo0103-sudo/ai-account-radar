from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import source_ingestion_lineage as lineage
import content_sampler
import wewe_provider_health as health
import ar034_rc_manifest
import daily_pipeline
import wewe_provider_refresh
import wechat_fulltext_provider_probe as provider_probe
from wewe_admin_chrome_runtime import marker_path, path_hash, verify_identity


class AR034SourceRecoveryTests(unittest.TestCase):
    def probe(self, failed_artifacts: int = 0, manual: Path | None = None) -> dict:
        payload = {
            "status": "completed_with_failures",
            "coverage": {
                "planned_accounts": 31, "attempted_accounts": 31, "successful_accounts": 29, "failed_account_count": 2,
                "failed_accounts": [{"account_name": "bad-a", "artifact_count": failed_artifacts}, {"account_name": "bad-b", "artifact_count": 0}],
                "per_account_artifact_counts": {**{f"ok-{i}": 3 for i in range(29)}, "bad-a": 0, "bad-b": 0},
                "invariants": {"attempted_equals_planned": True, "success_plus_failed_equals_attempted": True, "account_lineage_unique_and_complete": True},
            },
            "item_lineage": {"ok": True},
            "run_id": "run_20260716_080311",
        }
        if manual is not None:
            payload["manual_artifact"] = {"run_id": payload["run_id"], "path": str(manual.resolve()), "sha256": lineage.artifact_sha256(manual), "size": manual.stat().st_size, "row_count": 87}
        return payload

    def write_manual(self, path: Path) -> None:
        rows = [{"账号名/公众号名": f"ok-{i}", "内容指纹": f"fp-{i}-{j}", "运行批次": "run_20260716_080311"} for i in range(29) for j in range(3)]
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    def test_29_of_31_retains_all_87_and_bijection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manual = root / "manual.jsonl"; combined = root / "combined.jsonl"; csv_path = root / "content.csv"
            self.write_manual(manual); combined.write_bytes(manual.read_bytes())
            rows = [json.loads(line) for line in manual.read_text().splitlines()]
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["账号名/公众号名", "内容指纹", "运行批次"]); writer.writeheader(); writer.writerows(rows)
            report = lineage.validate_partial_source_artifact(self.probe(manual=manual), manual, expected_run_id="run_20260716_080311")
            self.assertEqual(report["successful_item_count"], 87)
            self.assertEqual(lineage.validate_ingestion_bijection(report, combined, csv_path)["source_to_survivor_count"], 87)

    def test_partial_lineage_mutations_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp) / "manual.jsonl"; self.write_manual(manual)
            for mutation in ("failed_artifact", "duplicate", "unknown", "missing"):
                probe = self.probe(1 if mutation == "failed_artifact" else 0, manual)
                if mutation == "duplicate":
                    manual.write_text(manual.read_text() + manual.read_text().splitlines()[0] + "\n")
                elif mutation == "unknown":
                    rows = manual.read_text().splitlines(); value = json.loads(rows[0]); value["账号名/公众号名"] = "unknown"; rows[0] = json.dumps(value); manual.write_text("\n".join(rows)+"\n")
                elif mutation == "missing":
                    manual.write_text("\n".join(manual.read_text().splitlines()[:-1])+"\n")
                with self.subTest(mutation=mutation), self.assertRaises(lineage.LineageError):
                    lineage.validate_partial_source_artifact(probe, manual, expected_run_id="run_20260716_080311")
                self.write_manual(manual)

    def test_combined_and_content_items_drift_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manual = root / "manual.jsonl"; combined = root / "combined.jsonl"; csv_path = root / "content.csv"
            self.write_manual(manual)
            report = lineage.validate_partial_source_artifact(self.probe(manual=manual), manual)
            rows = [json.loads(line) for line in manual.read_text().splitlines()]
            for layer in ("combined", "content_items"):
                combined.write_text("\n".join(json.dumps(row) for row in (rows[1:] if layer == "combined" else rows)) + "\n")
                csv_rows = rows[1:] if layer == "content_items" else rows
                with csv_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["账号名/公众号名", "内容指纹", "运行批次"]); writer.writeheader(); writer.writerows(csv_rows)
                with self.subTest(layer=layer), self.assertRaises(lineage.LineageError):
                    lineage.validate_ingestion_bijection(report, combined, csv_path)

    def test_comparison_and_feishu03_drift_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manual = root / "manual.jsonl"; combined = root / "combined.jsonl"; content = root / "content.csv"; comparison = root / "comparison.csv"
            self.write_manual(manual); combined.write_bytes(manual.read_bytes()); rows = [json.loads(line) for line in manual.read_text().splitlines()]
            for target, values in ((content, rows), (comparison, rows[1:])):
                with target.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["账号名/公众号名", "内容指纹", "运行批次"]); writer.writeheader(); writer.writerows(values)
            report = lineage.validate_partial_source_artifact(self.probe(manual=manual), manual)
            with self.assertRaisesRegex(lineage.LineageError, "comparison_universe"):
                lineage.validate_ingestion_bijection(report, combined, content, comparison)
            for readback, reason in ((None, "missing"), ({"ok": True, "run_id": "other", "ordered_fingerprints": report["ordered_fingerprints"]}, "run"), ({"ok": True, "run_id": report["run_id"], "ordered_fingerprints": report["ordered_fingerprints"][:-1]}, "identity")):
                with self.subTest(reason=reason), self.assertRaises(lineage.LineageError):
                    lineage.validate_feishu_readback_identity(report, readback, report["run_id"], write_mode=True)

    def test_manual_identity_missing_stale_or_wrong_run_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp) / "manual.jsonl"; self.write_manual(manual)
            for mutation in ("missing_identity", "wrong_run", "wrong_hash"):
                probe = self.probe(manual=manual)
                if mutation == "missing_identity": probe.pop("manual_artifact")
                elif mutation == "wrong_run": probe["run_id"] = "run_20260716_090000"
                else: probe["manual_artifact"]["sha256"] = "0" * 64
                with self.subTest(mutation=mutation), self.assertRaises(lineage.LineageError):
                    lineage.validate_partial_source_artifact(probe, manual, expected_run_id="run_20260716_080311")

    def test_daily_artifact_selection_rejects_missing_or_stale_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); primary_result = root / "primary.json"; primary_manual = root / "primary.jsonl"; retry_result = root / "retry.json"; retry_manual = root / "retry.jsonl"
            self.write_manual(primary_manual)
            primary_result.write_text(json.dumps(self.probe(manual=primary_manual)), encoding="utf-8")
            selected, _, report = daily_pipeline.select_and_validate_douyin_artifact("run_20260716_080311", primary_result, primary_manual, retry_result, retry_manual)
            self.assertEqual(selected, primary_result); self.assertEqual(report["successful_item_count"], 87)
            retry_result.write_text(primary_result.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaises(lineage.LineageError):
                daily_pipeline.select_and_validate_douyin_artifact("run_20260716_080311", primary_result, primary_manual, retry_result, retry_manual)

    def test_duplicate_and_cross_account_downstream_lineage_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manual = root / "manual.jsonl"; combined = root / "combined.jsonl"; csv_path = root / "content.csv"
            self.write_manual(manual); rows = [json.loads(line) for line in manual.read_text().splitlines()]
            report = lineage.validate_partial_source_artifact(self.probe(manual=manual), manual)
            for mutation in ("duplicate", "cross_account"):
                changed = [dict(row) for row in rows]
                if mutation == "duplicate": changed.append(dict(changed[0]))
                else: changed[0]["账号名/公众号名"] = "ok-2"
                combined.write_text("\n".join(json.dumps(row) for row in changed) + "\n")
                with csv_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["账号名/公众号名", "内容指纹", "运行批次"]); writer.writeheader(); writer.writerows(changed)
                with self.subTest(mutation=mutation), self.assertRaises(lineage.LineageError):
                    lineage.validate_ingestion_bijection(report, combined, csv_path)

    def test_wechat_typed_states(self) -> None:
        base = {"provider_reachable": True, "database_readable": True, "active_account_count": 1, "active_source_count": 1, "refresh_revision": 20, "refreshed_at_ms": 900, "new_item_count": 1}
        previous = {"refresh_revision": 10, "refreshed_at_ms": 700}
        attempt = {"run_id": "run", "status": "success", "attempt_id": "a", "started_at_ms": 810, "completed_at_ms": 950, "refresh_revision": 20, "refreshed_at_ms": 900}
        classify = lambda snapshot, att=attempt: health.classify_snapshot(snapshot, now_ms=1000, previous_watermark=previous, run_id="run", run_started_at_ms=800, refresh_attempt=att)
        self.assertEqual(classify(base)["state"], "updated_with_new_items")
        self.assertEqual(classify({**base, "new_item_count": 0})["state"], "updated_no_new_items")
        self.assertEqual(classify({**base, "refreshed_at_ms": 700})["state"], "stale_cache")
        self.assertEqual(classify({**base, "active_account_count": 0})["state"], "login_required")
        self.assertEqual(classify({**base, "provider_reachable": False})["state"], "provider_failed")
        self.assertEqual(classify(base, {**attempt, "attempt_id": ""})["state"], "stale_cache")
        self.assertEqual(classify(base, {**attempt, "status": "failed"})["state"], "stale_cache")
        self.assertEqual(classify({**base, "refreshed_at_ms": 1100}, {**attempt, "refreshed_at_ms": 1100})["state"], "stale_cache")

    def test_wechat_watermark_and_publish_time_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "last_success.json"
            self.assertEqual(health.load_success_watermark(state)["refresh_revision"], 0)
            state.write_text(json.dumps({"refresh_revision": 20, "refreshed_at_ms": 10, "article_publish_watermark": 100, "refresh_attempt_id": "a", "accepted_run_id": "run"}))
            self.assertEqual(health.load_success_watermark(state)["article_publish_watermark"], 100)
        self.assertEqual(provider_probe.published_epoch("2026-07-16T08:00:00+08:00"), 1784160000)
        self.assertEqual(provider_probe.published_epoch("1784160000000"), 1784160000)
        self.assertEqual(provider_probe.published_epoch("bad"), 0)

    def test_wechat_watermark_is_post_closure(self) -> None:
        source = Path(daily_pipeline.__file__).read_text(encoding="utf-8")
        self.assertLess(source.index('downstream_report.get("downstream_usable")'), source.index("commit_wechat_success_watermark" , source.index('downstream_report.get("downstream_usable")')))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "watermark.json"; target.write_bytes(b"original")
            freshness = {"state_path": str(target), "run_id": "run", "refresh_attempt_id": "attempt", "refresh_revision": 2, "refreshed_at_ms": 3, "latest_article_publish_time": 4}
            for downstream, closure in (({"downstream_usable": False}, {"feishu_03_identity": {"ok": True, "mode": "write"}}), ({"downstream_usable": True}, {"feishu_03_identity": {"ok": False, "mode": "write"}})):
                with self.assertRaises(RuntimeError): daily_pipeline.commit_wechat_success_watermark(freshness, downstream_report=downstream, ingestion_closure=closure, run_id="run")
                self.assertEqual(target.read_bytes(), b"original")

    def test_sampler_lineage_gate_precedes_feishu_write(self) -> None:
        source = Path(content_sampler.__file__).read_text(encoding="utf-8")
        main_source = source[source.index("def main() -> int:"):]
        self.assertLess(main_source.index("validate_ingestion_bijection("), main_source.index("write_content_ledger_to_feishu(items, run_id)"))

    def test_wewe_browser_identity_fail_closed(self) -> None:
        profile = Path("/tmp/wewe-profile").resolve(); version = {"webSocketDebuggerUrl": "ws://browser/one"}
        marker = {"marker_version": 1, "created_by": "start_wewe_rss_admin_chrome.py", "pid": 7, "port": 9334, "profile": str(profile), "profile_identity_hash": path_hash(profile), "browser_websocket_identity": "ws://browser/one"}
        files = [str(profile / "BrowserMetrics" / "BrowserMetrics-1.pma"), str(profile / "Default" / "History")]
        self.assertTrue(verify_identity(9334, profile, version, pid_reader=lambda _: [7], open_file_reader=lambda _: files, marker_reader=lambda _: marker).ok)
        for bad in ("port", "profile", "pid", "websocket", "marker"):
            kwargs = {"pid_reader": lambda _: [7], "open_file_reader": lambda _: files, "marker_reader": lambda _: marker}
            current_profile = profile; current_version = version
            if bad == "port": kwargs["pid_reader"] = lambda _: []
            elif bad == "profile": current_profile = Path("/tmp/other")
            elif bad == "pid": kwargs["pid_reader"] = lambda _: [8]
            elif bad == "websocket": current_version = {"webSocketDebuggerUrl": "ws://stale"}
            else: kwargs["marker_reader"] = lambda _: (_ for _ in ()).throw(FileNotFoundError())
            with self.subTest(bad=bad): self.assertFalse(verify_identity(9334, current_profile, current_version, **kwargs).ok)

    def test_scheduled_path_never_opens_or_discovers_wechat_browser(self) -> None:
        source = (Path(__file__).with_name("daily_pipeline.py")).read_text(encoding="utf-8")
        wechat_block = source[source.index("if args.fetch_wechat_fulltext_provider"):source.index("fetch_douyin =")]
        self.assertIn("wewe_provider_health.py", wechat_block)
        self.assertNotIn("start_wewe_rss.py", wechat_block)
        self.assertNotIn("start_wewe_rss_admin_chrome.py", wechat_block)
        self.assertNotIn("9334", wechat_block)

    def test_feishu03_readback_identity_is_exact(self) -> None:
        item = content_sampler.ContentItem(
            source_type="对标视频", platform="抖音", account_name="ok", title="title",
            url="https://example.com/1", content_shape="video", cover_text="", body_snippet="body",
            published_at="2026-07-16", comment_questions="", ocr_text="", fetch_method="cdp",
            fetch_status="ok", failure_reason="", fingerprint="fp-1",
        )
        good = [{"record_id": "rec-1", "fields": {"内容指纹": "fp-1", "运行批次": "run-1"}}]
        self.assertTrue(content_sampler.verify_content_ledger_readback([item], good, "run-1")["ok"])
        for mutation in ("missing", "wrong_run", "duplicate"):
            rows = [] if mutation == "missing" else [json.loads(json.dumps(good[0], ensure_ascii=False))]
            if mutation == "wrong_run": rows[0]["fields"]["运行批次"] = "other"
            if mutation == "duplicate": rows.append(json.loads(json.dumps(rows[0], ensure_ascii=False)))
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                content_sampler.verify_content_ledger_readback([item], rows, "run-1")

    def test_rc_manifest_requires_exact_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for value in ("11fab14", "G" * 40, "", None):
                self.assertFalse(ar034_rc_manifest.verify_manifest({"rc_head": value}, repo)["ok"])
            exact = "a" * 40
            completed = mock.Mock(stdout=exact + "\n")
            with mock.patch.object(ar034_rc_manifest.subprocess, "run", return_value=completed):
                self.assertTrue(ar034_rc_manifest.verify_manifest({"rc_head": exact}, repo)["ok"])
                self.assertEqual(ar034_rc_manifest.verify_manifest({"rc_head": "b" * 40}, repo)["reason"], "rc_head_mismatch")


if __name__ == "__main__":
    unittest.main()
