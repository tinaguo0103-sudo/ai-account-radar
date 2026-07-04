#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import codex_script_package_runner as runner
import learn_from_daily_feedback


class CodexScriptPackageClickableLinksTest(unittest.TestCase):
    def test_existing_text_field_keeps_plain_url_not_markdown(self) -> None:
        fields = runner.format_script_package_record_fields(
            {
                "飞书文档": "https://my.feishu.cn/docx/doc_test",
                "飞书文件夹": "https://my.feishu.cn/drive/folder/folder_test",
                "文档同步状态": "已创建飞书文档，但需确认文件夹对用户可见",
            },
            {
                "飞书文档": {"type": 1},
                "飞书文件夹": {"type": 1},
                "文档同步状态": {"type": 1},
            },
        )

        self.assertEqual(fields["飞书文档"], "https://my.feishu.cn/docx/doc_test")
        self.assertEqual(fields["飞书文件夹"], "https://my.feishu.cn/drive/folder/folder_test")
        self.assertNotIn("[打开飞书文档]", str(fields["飞书文档"]))
        self.assertEqual(fields["文档同步状态"], "已创建飞书文档，但需确认文件夹对用户可见")

    def test_url_field_uses_url_payload(self) -> None:
        fields = runner.format_script_package_record_fields(
            {"飞书文档": "https://my.feishu.cn/docx/doc_test"},
            {"飞书文档": {"type": 15}},
        )

        self.assertEqual(fields["飞书文档"], {
            "text": "打开飞书文档",
            "link": "https://my.feishu.cn/docx/doc_test",
        })

    def test_optional_url_mirror_field_is_populated_when_present(self) -> None:
        fields = runner.format_script_package_record_fields(
            {"飞书文档": "https://my.feishu.cn/docx/doc_test"},
            {
                "飞书文档": {"type": 1},
                "飞书文档链接": {"type": 15},
            },
        )

        self.assertEqual(fields["飞书文档"], "https://my.feishu.cn/docx/doc_test")
        self.assertEqual(fields["飞书文档链接"], {
            "text": "打开飞书文档",
            "link": "https://my.feishu.cn/docx/doc_test",
        })

    def test_empty_link_stays_empty_and_status_fields_are_unchanged(self) -> None:
        fields = runner.format_script_package_record_fields(
            {
                "飞书文档": "",
                "脚本状态": "已生成完整脚本包",
                "文档同步状态": "飞书文档同步失败",
                "文档同步错误": "missing folder token",
            },
            {"飞书文档": {"type": 1}},
        )

        self.assertEqual(fields["飞书文档"], "")
        self.assertEqual(fields["脚本状态"], "已生成完整脚本包")
        self.assertEqual(fields["文档同步状态"], "飞书文档同步失败")
        self.assertEqual(fields["文档同步错误"], "missing folder token")

    def test_create_record_formats_links_after_reading_field_metadata(self) -> None:
        calls: list[dict] = []

        def fake_request_json(_method: str, _path: str, *, token: str, body: dict):
            calls.append(body)
            return {"data": {"record": {"record_id": "rec_test"}}}

        with patch.object(runner, "fields_by_name", return_value={"飞书文档": {"type": 1}, "飞书文档链接": {"type": 15}}), \
                patch.object(runner.feishu, "request_json", side_effect=fake_request_json):
            record_id = runner.create_script_package_record(
                "token",
                "app",
                "table",
                {
                    "脚本标题": "测试脚本",
                    "飞书文档": "https://my.feishu.cn/docx/doc_test",
                    "本地文档": str(Path("/private/tmp/test.md")),
                },
            )

        self.assertEqual(record_id, "rec_test")
        self.assertEqual(calls[0]["fields"]["飞书文档"], "https://my.feishu.cn/docx/doc_test")
        self.assertEqual(calls[0]["fields"]["飞书文档链接"]["link"], "https://my.feishu.cn/docx/doc_test")
        self.assertEqual(calls[0]["fields"]["飞书文档链接"]["text"], "打开飞书文档")

    def test_learning_feedback_reads_rich_text_link_as_url(self) -> None:
        value = [{"type": "url", "text": "打开飞书文档", "link": "https://my.feishu.cn/docx/doc_test"}]

        self.assertEqual(learn_from_daily_feedback.normalize(value), "https://my.feishu.cn/docx/doc_test")


if __name__ == "__main__":
    unittest.main()
