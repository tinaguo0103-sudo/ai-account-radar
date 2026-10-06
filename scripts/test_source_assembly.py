from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from source_assembly import (
    SOURCE_IDS,
    SourcePackageError,
    import_source_package,
)
from daily_pipeline import combine_manual_jsonl, douyin_source_run, provider_source_run
from content_sampler import ContentItem, item_row


RUN_ID = "run_20261006_081500"
BUSINESS_DATE = "2026-10-06"
ATTEMPTED_AT = "2026-10-06T00:15:00+00:00"


def runtime_config() -> dict:
    return {
        "config_revision": 3,
        "sources": [
            {"id": source_id, "verified_identity": f"verified:{source_id}"}
            for source_id in sorted(SOURCE_IDS)
        ],
    }


def write_package(root: Path, *, revision: int = 3, run_id: str = RUN_ID) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "raw").mkdir()
    body = "First-party source body with enough inspectable text."
    raw = body.encode("utf-8")
    (root / "raw" / "openai.txt").write_bytes(raw)
    (root / "raw" / "anthropic.txt").write_bytes(raw)
    shared_url = "https://openai.com/news/release?b=2&a=1"
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "business_date": BUSINESS_DATE,
        "config_revision": revision,
        "source_ids": sorted(SOURCE_IDS),
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    receipts = []
    for source_id in sorted(SOURCE_IDS):
        if source_id == "feishu_recvl4uBak96UM":
            status, attempted, attempt_count, item_count, reason = "completed", True, 1, 1, ""
        elif source_id == "feishu_recvl4uBakM7LI":
            status, attempted, attempt_count, item_count, reason = "completed", True, 1, 1, ""
        else:
            status, attempted, attempt_count, item_count, reason = "blocked", False, 0, 0, "not_attempted_waiting_owner"
        receipts.append({
            "run_id": run_id,
            "source_id": source_id,
            "verified_identity": f"verified:{source_id}",
            "attempted": attempted,
            "attempted_at": ATTEMPTED_AT if attempted else "",
            "attempt_count": attempt_count,
            "failed_attempt_count": 0,
            "status": status,
            "item_count": item_count,
            "visible_list_complete": False,
            "error_summary": reason,
            "completed_at": ATTEMPTED_AT if attempted else "",
        })
    (root / "receipts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in receipts), encoding="utf-8",
    )
    items = []
    for source_id, item_id, raw_path in [
        ("feishu_recvl4uBak96UM", "openai-release", "raw/openai.txt"),
        ("feishu_recvl4uBakM7LI", "anthropic-release", "raw/anthropic.txt"),
    ]:
        items.append({
            "run_id": run_id,
            "source_id": source_id,
            "item_id": item_id,
            "title": "Release notes",
            "url": shared_url,
            "author": source_id,
            "published_at": "2026-10-05",
            "raw_path": raw_path,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        })
    (root / "items.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in items), encoding="utf-8",
    )
    return root


class SourceAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = write_package(self.root / "package")
        self.run_root = self.root / "runs" / RUN_ID

    def tearDown(self) -> None:
        self.temp.cleanup()

    def import_package(self, package: Path | None = None, *, run_id: str = RUN_ID, date: str = BUSINESS_DATE):
        if package is None:
            package = self.package
        return import_source_package(
            package,
            run_id=run_id,
            business_date=date,
            run_root=self.run_root,
            runtime_config=runtime_config(),
        )

    def test_exact_package_deduplicates_url_and_preserves_every_source_identity(self) -> None:
        result = self.import_package()
        self.assertEqual(2, result["raw_item_count"])
        self.assertEqual(1, result["deduplicated_item_count"])
        self.assertEqual(1, result["duplicate_url_count"])
        row = json.loads(result["manual_path"].read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("url:https://openai.com/news/release?a=1&b=2", row["item_id"])
        self.assertEqual(
            {"feishu_recvl4uBak96UM", "feishu_recvl4uBakM7LI"},
            {source["source_id"] for source in row["source_provenance"]},
        )
        self.assertTrue(Path(row["raw_artifact_path"]).is_file())
        unattempted = next(row for row in result["source_runs"] if row["source"] == "x")
        self.assertEqual("not_attempted", unattempted["status"])
        self.assertEqual(0, unattempted["item_count"])

    def test_successful_package_checkpoint_is_reused_without_package_path(self) -> None:
        first = self.import_package()
        second = import_source_package(
            None,
            run_id=RUN_ID,
            business_date=BUSINESS_DATE,
            run_root=self.run_root,
            runtime_config=runtime_config(),
        )
        self.assertTrue(second["reused"])
        self.assertEqual(first["manual_path"], second["manual_path"])
        self.assertEqual(first["input_sha256"], second["input_sha256"])

    def test_wrong_run_and_wrong_revision_fail_before_writing(self) -> None:
        with self.assertRaisesRegex(SourcePackageError, "wrong_run"):
            self.import_package(self.package, run_id="run_20261006_081501")
        self.assertFalse(self.run_root.exists())
        bad_revision = write_package(self.root / "bad-revision", revision=2)
        with self.assertRaisesRegex(SourcePackageError, "revision_mismatch"):
            self.import_package(bad_revision)
        self.assertFalse(self.run_root.exists())

    def test_wrong_date_fails_closed(self) -> None:
        manifest_path = self.package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["business_date"] = "2026-10-05"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(SourcePackageError, "wrong_business_date"):
            self.import_package()
        self.assertFalse(self.run_root.exists())

    def test_absolute_and_parent_raw_paths_fail_before_materializing_any_rows(self) -> None:
        for invalid in ("../outside.txt", str(self.root / "outside.txt")):
            with self.subTest(invalid=invalid):
                package = write_package(self.root / f"bad-{len(invalid)}")
                items_path = package / "items.jsonl"
                rows = [json.loads(line) for line in items_path.read_text().splitlines()]
                rows[0]["raw_path"] = invalid
                items_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                with self.assertRaisesRegex(SourcePackageError, "path_invalid|path_escape|raw_path_invalid"):
                    self.import_package(package)
                self.assertFalse(self.run_root.exists())

    def test_mutated_raw_bytes_and_unknown_source_are_rejected(self) -> None:
        (self.package / "raw" / "openai.txt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(SourcePackageError, "raw_hash_mismatch"):
            self.import_package()
        self.assertFalse(self.run_root.exists())

        package = write_package(self.root / "unknown-source")
        receipts_path = package / "receipts.jsonl"
        receipts = [json.loads(line) for line in receipts_path.read_text().splitlines()]
        receipts[0]["source_id"] = "unknown-source-id"
        receipts_path.write_text("".join(json.dumps(row) + "\n" for row in receipts), encoding="utf-8")
        with self.assertRaisesRegex(SourcePackageError, "unknown_source"):
            self.import_package(package)
        self.assertFalse(self.run_root.exists())

    def test_visible_empty_requires_an_actual_complete_list_attempt(self) -> None:
        receipts_path = self.package / "receipts.jsonl"
        receipts = [json.loads(line) for line in receipts_path.read_text().splitlines()]
        receipt = next(row for row in receipts if row["source_id"] == "feishu_recvl4uBakDz73")
        receipt.update({
            "attempted": True, "attempted_at": ATTEMPTED_AT, "attempt_count": 1,
            "status": "completed_empty", "visible_list_complete": False,
        })
        receipts_path.write_text("".join(json.dumps(row) + "\n" for row in receipts), encoding="utf-8")
        with self.assertRaisesRegex(SourcePackageError, "empty_not_observed"):
            self.import_package()
        self.assertFalse(self.run_root.exists())

    def test_same_run_changed_package_cannot_replace_checkpoint(self) -> None:
        self.import_package()
        changed = write_package(self.root / "changed")
        (changed / "raw" / "openai.txt").write_text("different but valid source body", encoding="utf-8")
        items_path = changed / "items.jsonl"
        rows = [json.loads(line) for line in items_path.read_text().splitlines()]
        rows[0]["raw_sha256"] = hashlib.sha256((changed / rows[0]["raw_path"]).read_bytes()).hexdigest()
        items_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        with self.assertRaisesRegex(SourcePackageError, "same_run_conflict"):
            self.import_package(changed)

    def test_source_budget_is_checked_before_materializing_rows(self) -> None:
        items_path = self.package / "items.jsonl"
        rows = [json.loads(line) for line in items_path.read_text().splitlines()]
        template = rows[0]
        for index in range(5):
            rows.append({
                **template,
                "item_id": f"openai-{index}",
                "url": f"https://openai.com/news/release-{index}",
            })
        items_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        receipt_path = self.package / "receipts.jsonl"
        receipts = [json.loads(line) for line in receipt_path.read_text().splitlines()]
        next(row for row in receipts if row["source_id"] == "feishu_recvl4uBak96UM")["item_count"] = 6
        receipt_path.write_text("".join(json.dumps(row) + "\n" for row in receipts), encoding="utf-8")
        with self.assertRaisesRegex(SourcePackageError, "source_budget_exceeded"):
            self.import_package()
        self.assertFalse(self.run_root.exists())

    def test_manual_merge_and_source_run_aggregation_preserve_local_failure(self) -> None:
        first = self.root / "one.jsonl"
        second = self.root / "two.jsonl"
        first.write_text(json.dumps({
            "内容链接": "https://example.com/shared", "内容标题": "shared",
            "source_provenance": [{"source_id": "source-a", "url": "https://example.com/shared"}],
        }) + "\n", encoding="utf-8")
        second.write_text(json.dumps({
            "内容链接": "https://example.com/shared", "内容标题": "shared",
            "source_provenance": [{"source_id": "source-b", "url": "https://example.com/shared"}],
        }) + "\n", encoding="utf-8")
        merged = combine_manual_jsonl([first, second], self.root / "combined.jsonl")
        row = json.loads(merged.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual({"source-a", "source-b"}, {item["source_id"] for item in row["source_provenance"]})

        wechat = provider_source_run(RUN_ID, "wechat", {
            "status": "success", "attempted_at": ATTEMPTED_AT,
            "source_id": "feishu_recvlm0QvTIDMe", "rows": 1,
        })
        not_attempted = provider_source_run(RUN_ID, "wechat", {
            "status": "blocked", "source_id": "feishu_recvlm0QvTIDMe",
        })
        self.assertEqual("completed", wechat["status"])
        self.assertEqual("not_attempted", not_attempted["status"])
        douyin = douyin_source_run(RUN_ID, {
            "coverage": {"planned_accounts": 31},
            "rows": [
                {"attempted_at": ATTEMPTED_AT, "status": "success", "artifact_count": 2},
                {"attempted_at": ATTEMPTED_AT, "status": "timeout", "artifact_count": 0},
                {"status": "not_attempted_source_runtime_failure"},
            ],
        })
        self.assertEqual(("partial", 31, 1, 1), (
            douyin["status"], douyin["planned_count"],
            douyin["succeeded_count"], douyin["failed_count"],
        ))

    def test_package_body_enters_content_sampler_as_full_text_with_raw_path_and_identity(self) -> None:
        item = ContentItem(
            source_type="公开网页", platform="OpenAI", account_name="OpenAI",
            title="Release", url="https://openai.com/news/release",
            content_shape="public_article", cover_text="", body_snippet="正文" * 400,
            published_at="2026-10-05", comment_questions="", ocr_text="",
            fetch_method="source_package_raw", fetch_status="success", failure_reason="",
            fingerprint="item-1", raw_payload_path="/private/tmp/run/raw.txt",
            source_provenance=[{
                "source_id": "feishu_recvl4uBak96UM", "source": "openai_news",
                "raw_sha256": "a" * 64, "raw_artifact_path": "/private/tmp/run/raw.txt",
            }],
        )
        row = item_row(item)
        self.assertEqual("是", row["是否全文解析"])
        self.assertEqual("/private/tmp/run/raw.txt", row["原始payload路径"])
        provenance = json.loads(row["source_provenance"])
        self.assertEqual("feishu_recvl4uBak96UM", provenance[0]["source_id"])
        self.assertEqual("a" * 64, provenance[0]["raw_sha256"])


if __name__ == "__main__":
    unittest.main()
