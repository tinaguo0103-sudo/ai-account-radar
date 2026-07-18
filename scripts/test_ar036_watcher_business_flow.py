from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex_script_package_runner as runner


class WatcherBusinessFlowTests(unittest.TestCase):
    def test_ready_item_generates_once_with_link_payload_and_second_run_is_noop(self) -> None:
        record = {"record_id": "rec-ready", "fields": {"04是否已生成06": ""}}
        topic = {"topic_title": "测试 watcher 永久修复"}
        package = {
            "topic_title": topic["topic_title"],
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
        queue = {"ready": True}
        requests: list[tuple[str, str, dict]] = []

        def load_ready_topics(_record_id: str, _limit: int):
            records = [record] if queue["ready"] else []
            topics = [topic] if queue["ready"] else []
            return "token", "app", {"topic_decision": "04", "script_package": "06"}, records, topics

        def request_json(method: str, path: str, **kwargs):
            requests.append((method, path, kwargs.get("body") or {}))
            if method == "POST":
                return {"data": {"record": {"record_id": "rec-06"}}}
            if method == "PUT":
                queue["ready"] = False
                return {"data": {}}
            raise AssertionError((method, path))

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {
                    "SCRIPT_PACKAGE_OUTPUT_ROOT": tmp,
                    "SCRIPT_PACKAGE_DISPLAY_OUTPUT_ROOT": tmp,
                }), \
                mock.patch.object(runner, "load_local_env"), \
                mock.patch.object(runner, "codex_runtime_preflight", return_value={"ok": True}), \
                mock.patch.object(runner, "acquire_lock", return_value=object()), \
                mock.patch.object(runner, "load_ready_topics", side_effect=load_ready_topics), \
                mock.patch.object(runner, "ensure_text_fields"), \
                mock.patch.object(runner, "generate_package_with_retry", return_value=(package, 1, [])) as generate, \
                mock.patch.object(runner, "try_create_feishu_document", return_value=runner.FeishuDocSyncResult(
                    url="https://my.feishu.cn/docx/doc_fixture",
                    folder_url="https://my.feishu.cn/drive/folder/folder_fixture",
                    status="飞书文档同步成功",
                )), \
                mock.patch.object(runner, "fields_by_name", return_value={
                    "飞书文档": {"type": 1},
                    "飞书文档链接": {"type": 15},
                    "飞书文件夹": {"type": 1},
                    "飞书文件夹链接": {"type": 15},
                }), \
                mock.patch.object(runner.feishu, "request_json", side_effect=request_json), \
                mock.patch.object(runner.time, "sleep"), \
                mock.patch.object(runner.sys, "argv", ["runner", "--write-feishu", "--limit", "1"]):
            self.assertEqual(0, runner.main())
            self.assertEqual(0, runner.main())
            markdown_files = list(Path(tmp).glob("*.md"))

        self.assertEqual(1, generate.call_count)
        self.assertEqual(1, len(markdown_files))
        post_calls = [call for call in requests if call[0] == "POST"]
        self.assertEqual(1, len(post_calls))
        fields = post_calls[0][2]["fields"]
        self.assertEqual(
            {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/doc_fixture"},
            fields["飞书文档链接"],
        )
        self.assertEqual(
            {"text": "打开飞书文件夹", "link": "https://my.feishu.cn/drive/folder/folder_fixture"},
            fields["飞书文件夹链接"],
        )
        self.assertEqual(1, len([call for call in requests if call[0] == "PUT"]))


if __name__ == "__main__":
    unittest.main()
