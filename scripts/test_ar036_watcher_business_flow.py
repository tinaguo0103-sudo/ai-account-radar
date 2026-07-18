from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex_script_package_runner as runner


class PublicRunnerFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.record = {"record_id": "rec-ready", "fields": {"运行批次": "run_20260718_080000"}}
        self.topic = {"topic_title": "测试 watcher 永久修复"}
        self.package = {
            "topic_title": self.topic["topic_title"],
            "recommended_template": "测试模板",
            "core_viewpoint": "只生成一次",
            "opening_hook": "测试开头",
            "material_reminders": [],
            "release_checks": [],
            "qa_status": "pass",
            "qa_result": "fixture pass",
            "can_shoot": "是",
            "full_markdown": "# 测试 watcher 永久修复\n\n只有一份脚本。",
        }
        self.ready = True
        self.records06: list[dict] = []
        self.marker = ""
        self.counts = {"generate": 0, "markdown": 0, "document": 0, "post": 0, "marker": 0}
        self.post_modes: list[str] = []
        self.readback_error = False

    def load_ready_topics(self, _record_id: str, _limit: int):
        records = [self.record] if self.ready else []
        topics = [self.topic] if self.ready else []
        return "token", "app", {"topic_decision": "04", "script_package": "06"}, records, topics

    def generate(self, _topic: dict, _timeout: int):
        self.counts["generate"] += 1
        return self.package, 1, []

    def markdown(self, topic: dict, package: dict):
        self.counts["markdown"] += 1
        path = self.root / "package.md"
        path.write_text(str(package["full_markdown"]).rstrip() + "\n", encoding="utf-8")
        return path

    def document(self, _token: str, _title: str, _package: dict):
        self.counts["document"] += 1
        return runner.FeishuDocSyncResult(
            url="https://my.feishu.cn/docx/doc_fixture",
            folder_url="https://my.feishu.cn/drive/folder/folder_fixture",
            status="飞书文档同步成功",
        )

    def all_records(self, _token: str, _app: str, _table: str):
        if self.readback_error:
            raise RuntimeError("readback unavailable")
        return list(self.records06)

    def create(self, _token: str, _app: str, _table: str, row: dict):
        self.counts["post"] += 1
        mode = self.post_modes.pop(0) if self.post_modes else "success"
        if mode == "before_commit":
            raise RuntimeError("Feishu API rejected request before commit")
        if mode == "unknown_without_commit":
            raise RuntimeError("POST status unknown before server commit could be determined")
        record = {"record_id": "rec-06", "fields": dict(row)}
        self.records06.append(record)
        if mode == "after_commit_unknown":
            raise RuntimeError("POST status unknown after server commit")
        return "rec-06"

    def mark(self, _token: str, _app: str, _table: str, _record_id: str, marker: str = "是"):
        self.counts["marker"] += 1
        self.marker = marker
        self.ready = False

    def marker_value(self, *_args):
        return self.marker

    def patches(self):
        return (
            mock.patch.object(runner, "TRANSACTION_ROOT", self.root / "transactions"),
            mock.patch.object(runner, "load_local_env"),
            mock.patch.object(runner, "codex_runtime_preflight", return_value={"ok": True}),
            mock.patch.object(runner, "acquire_lock", return_value=object()),
            mock.patch.object(runner, "load_ready_topics", side_effect=self.load_ready_topics),
            mock.patch.object(runner, "ensure_text_fields"),
            mock.patch.object(runner, "generate_package_with_retry", side_effect=self.generate),
            mock.patch.object(runner, "write_package_markdown", side_effect=self.markdown),
            mock.patch.object(runner, "try_create_feishu_document", side_effect=self.document),
            mock.patch.object(runner, "all_records", side_effect=self.all_records),
            mock.patch.object(runner, "create_script_package_record", side_effect=self.create),
            mock.patch.object(runner, "mark_topic_generated", side_effect=self.mark),
            mock.patch.object(runner, "topic_marker_value", side_effect=self.marker_value),
            mock.patch.object(runner.time, "sleep"),
            mock.patch.object(runner.sys, "argv", ["runner", "--write-feishu", "--limit", "1"]),
        )

    def run(self) -> int:
        patches = self.patches()
        for patcher in patches:
            patcher.start()
        try:
            return runner.main()
        finally:
            for patcher in reversed(patches):
                patcher.stop()


class WatcherBusinessFlowTests(unittest.TestCase):
    def test_happy_path_and_second_poll_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicRunnerFixture(Path(tmp))
            self.assertEqual(0, fixture.run())
            self.assertEqual(0, fixture.run())
            self.assertEqual(
                {"generate": 1, "markdown": 1, "document": 1, "post": 1, "marker": 1},
                fixture.counts,
            )
            self.assertEqual(1, len(fixture.records06))

    def test_after_commit_response_loss_reconciles_without_second_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicRunnerFixture(Path(tmp))
            fixture.post_modes = ["after_commit_unknown"]
            self.assertEqual(0, fixture.run())
            self.assertEqual(0, fixture.run())
            self.assertEqual(
                {"generate": 1, "markdown": 1, "document": 1, "post": 1, "marker": 1},
                fixture.counts,
            )
            self.assertEqual(1, len(fixture.records06))

    def test_before_commit_failure_reuses_package_and_document_for_bounded_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicRunnerFixture(Path(tmp))
            fixture.post_modes = ["before_commit", "success"]
            self.assertEqual(4, fixture.run())
            self.assertEqual(0, fixture.run())
            self.assertEqual(
                {"generate": 1, "markdown": 1, "document": 1, "post": 2, "marker": 1},
                fixture.counts,
            )
            self.assertEqual(1, len(fixture.records06))

    def test_document_is_reused_after_known_post_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicRunnerFixture(Path(tmp))
            fixture.post_modes = ["before_commit", "success"]
            self.assertEqual(4, fixture.run())
            self.assertEqual(0, fixture.run())
            self.assertEqual(1, fixture.counts["document"])

    def test_duplicate_exact_06_stops_before_generation_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicRunnerFixture(Path(tmp))
            row = {"关联选题": fixture.topic["topic_title"], "脚本标题": fixture.topic["topic_title"]}
            fixture.records06 = [
                {"record_id": "rec-06-a", "fields": row},
                {"record_id": "rec-06-b", "fields": row},
            ]
            self.assertEqual(4, fixture.run())
            self.assertEqual({"generate": 0, "markdown": 0, "document": 0, "post": 0, "marker": 0}, fixture.counts)

    def test_existing_exact_06_only_repairs_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicRunnerFixture(Path(tmp))
            fixture.records06 = [{
                "record_id": "rec-06",
                "fields": {"关联选题": fixture.topic["topic_title"], "脚本标题": fixture.topic["topic_title"]},
            }]
            self.assertEqual(0, fixture.run())
            self.assertEqual({"generate": 0, "markdown": 0, "document": 0, "post": 0, "marker": 1}, fixture.counts)
            self.assertEqual(1, len(fixture.records06))

    def test_readback_failure_stops_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicRunnerFixture(Path(tmp))
            fixture.readback_error = True
            self.assertEqual(4, fixture.run())
            self.assertEqual({"generate": 0, "markdown": 0, "document": 0, "post": 0, "marker": 0}, fixture.counts)

    def test_unknown_without_committed_readback_never_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicRunnerFixture(Path(tmp))
            fixture.post_modes = ["unknown_without_commit"]
            self.assertEqual(4, fixture.run())
            self.assertEqual(4, fixture.run())
            self.assertEqual(1, fixture.counts["post"])
            self.assertEqual(1, fixture.counts["generate"])
            self.assertEqual(1, fixture.counts["document"])

    def test_link_type_15_payload_remains_object_shaped(self) -> None:
        row = {
            "飞书文档": "https://my.feishu.cn/docx/doc_fixture",
            "飞书文件夹": "https://my.feishu.cn/drive/folder/folder_fixture",
        }
        fields = runner.format_script_package_record_fields(row, {
            "飞书文档": {"type": 1}, "飞书文档链接": {"type": 15},
            "飞书文件夹": {"type": 1}, "飞书文件夹链接": {"type": 15},
        })
        self.assertEqual({"text": "打开飞书文档", "link": row["飞书文档"]}, fields["飞书文档链接"])
        self.assertEqual({"text": "打开飞书文件夹", "link": row["飞书文件夹"]}, fields["飞书文件夹链接"])


if __name__ == "__main__":
    unittest.main()
