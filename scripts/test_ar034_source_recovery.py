from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import source_ingestion_lineage as lineage
import content_sampler
import wewe_provider_health as health
import wechat_fulltext_provider_probe as provider_probe
from wewe_admin_chrome_runtime import marker_path, path_hash, verify_identity


class AR034SourceRecoveryTests(unittest.TestCase):
    def probe(self, failed_artifacts: int = 0) -> dict:
        return {
            "status": "completed_with_failures",
            "coverage": {
                "planned_accounts": 31, "attempted_accounts": 31, "successful_accounts": 29, "failed_account_count": 2,
                "failed_accounts": [{"account_name": "bad-a", "artifact_count": failed_artifacts}, {"account_name": "bad-b", "artifact_count": 0}],
                "per_account_artifact_counts": {**{f"ok-{i}": 3 for i in range(29)}, "bad-a": 0, "bad-b": 0},
                "invariants": {"attempted_equals_planned": True, "success_plus_failed_equals_attempted": True, "account_lineage_unique_and_complete": True},
            },
            "item_lineage": {"ok": True},
        }

    def write_manual(self, path: Path) -> None:
        rows = [{"账号名/公众号名": f"ok-{i}", "内容指纹": f"fp-{i}-{j}"} for i in range(29) for j in range(3)]
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    def test_29_of_31_retains_all_87_and_bijection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manual = root / "manual.jsonl"; combined = root / "combined.jsonl"; csv_path = root / "content.csv"
            self.write_manual(manual); combined.write_bytes(manual.read_bytes())
            rows = [json.loads(line) for line in manual.read_text().splitlines()]
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["账号名/公众号名", "内容指纹"]); writer.writeheader(); writer.writerows(rows)
            report = lineage.validate_partial_source_artifact(self.probe(), manual)
            self.assertEqual(report["successful_item_count"], 87)
            self.assertEqual(lineage.validate_ingestion_bijection(report, combined, csv_path)["source_to_survivor_count"], 87)

    def test_partial_lineage_mutations_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp) / "manual.jsonl"; self.write_manual(manual)
            for mutation in ("failed_artifact", "duplicate", "unknown", "missing"):
                probe = self.probe(1 if mutation == "failed_artifact" else 0)
                if mutation == "duplicate":
                    manual.write_text(manual.read_text() + manual.read_text().splitlines()[0] + "\n")
                elif mutation == "unknown":
                    rows = manual.read_text().splitlines(); value = json.loads(rows[0]); value["账号名/公众号名"] = "unknown"; rows[0] = json.dumps(value); manual.write_text("\n".join(rows)+"\n")
                elif mutation == "missing":
                    manual.write_text("\n".join(manual.read_text().splitlines()[:-1])+"\n")
                with self.subTest(mutation=mutation), self.assertRaises(lineage.LineageError):
                    lineage.validate_partial_source_artifact(probe, manual)
                self.write_manual(manual)

    def test_combined_and_content_items_drift_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manual = root / "manual.jsonl"; combined = root / "combined.jsonl"; csv_path = root / "content.csv"
            self.write_manual(manual)
            report = lineage.validate_partial_source_artifact(self.probe(), manual)
            rows = [json.loads(line) for line in manual.read_text().splitlines()]
            for layer in ("combined", "content_items"):
                combined.write_text("\n".join(json.dumps(row) for row in (rows[1:] if layer == "combined" else rows)) + "\n")
                csv_rows = rows[1:] if layer == "content_items" else rows
                with csv_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["账号名/公众号名", "内容指纹"]); writer.writeheader(); writer.writerows(csv_rows)
                with self.subTest(layer=layer), self.assertRaises(lineage.LineageError):
                    lineage.validate_ingestion_bijection(report, combined, csv_path)

    def test_duplicate_and_cross_account_downstream_lineage_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manual = root / "manual.jsonl"; combined = root / "combined.jsonl"; csv_path = root / "content.csv"
            self.write_manual(manual); rows = [json.loads(line) for line in manual.read_text().splitlines()]
            report = lineage.validate_partial_source_artifact(self.probe(), manual)
            for mutation in ("duplicate", "cross_account"):
                changed = [dict(row) for row in rows]
                if mutation == "duplicate": changed.append(dict(changed[0]))
                else: changed[0]["账号名/公众号名"] = "ok-2"
                combined.write_text("\n".join(json.dumps(row) for row in changed) + "\n")
                with csv_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["账号名/公众号名", "内容指纹"]); writer.writeheader(); writer.writerows(changed)
                with self.subTest(mutation=mutation), self.assertRaises(lineage.LineageError):
                    lineage.validate_ingestion_bijection(report, combined, csv_path)

    def test_wechat_typed_states(self) -> None:
        base = {"provider_reachable": True, "database_readable": True, "active_account_count": 1, "active_source_count": 1, "refresh_revision": 20, "refreshed_at_ms": 900, "new_item_count": 1}
        self.assertEqual(health.classify_snapshot(base, now_ms=1000, previous_success_revision=10)["state"], "updated_with_new_items")
        self.assertEqual(health.classify_snapshot({**base, "new_item_count": 0}, now_ms=1000, previous_success_revision=20)["state"], "updated_no_new_items")
        self.assertEqual(health.classify_snapshot({**base, "refreshed_at_ms": 1}, now_ms=100000000)["state"], "stale_cache")
        self.assertEqual(health.classify_snapshot({**base, "active_account_count": 0}, now_ms=1000)["state"], "login_required")
        self.assertEqual(health.classify_snapshot({**base, "provider_reachable": False}, now_ms=1000)["state"], "provider_failed")
        self.assertEqual(health.classify_snapshot(base, now_ms=1000, previous_success_revision=0)["state"], "stale_cache")

    def test_wechat_watermark_and_publish_time_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "last_success.json"
            self.assertEqual(health.load_success_watermark(state), {"refresh_revision": 0, "article_publish_watermark": 0})
            state.write_text(json.dumps({"refresh_revision": 20, "article_publish_watermark": 100}))
            self.assertEqual(health.load_success_watermark(state)["article_publish_watermark"], 100)
        self.assertEqual(provider_probe.published_epoch("2026-07-16T08:00:00+08:00"), 1784160000)
        self.assertEqual(provider_probe.published_epoch("1784160000000"), 1784160000)
        self.assertEqual(provider_probe.published_epoch("bad"), 0)

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


if __name__ == "__main__":
    unittest.main()
