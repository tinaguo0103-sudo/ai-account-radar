#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import codex_script_package_runner as runner


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

    def test_empty_optional_link_field_is_omitted(self) -> None:
        fields = runner.format_script_package_record_fields(
            {"飞书文档": "", "飞书文档链接": "stale"},
            {"飞书文档": {"type": 1}, "飞书文档链接": {"type": 15}},
        )

        self.assertEqual(fields["飞书文档"], "")
        self.assertNotIn("飞书文档链接", fields)

    def test_document_and_folder_link_fields_use_exact_link_objects(self) -> None:
        fields = runner.format_script_package_record_fields(
            {
                "飞书文档": "https://my.feishu.cn/docx/doc_test",
                "飞书文件夹": "https://my.feishu.cn/drive/folder/folder_test",
                "脚本状态": "已生成完整脚本包",
            },
            {
                "飞书文档": {"type": 1},
                "飞书文档链接": {"type": 15},
                "飞书文件夹": {"type": 1},
                "飞书文件夹链接": {"type": 15},
                "脚本状态": {"type": 1},
            },
        )

        self.assertEqual(fields["飞书文档链接"], {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/doc_test"})
        self.assertEqual(fields["飞书文件夹链接"], {"text": "打开飞书文件夹", "link": "https://my.feishu.cn/drive/folder/folder_test"})
        self.assertEqual(fields["脚本状态"], "已生成完整脚本包")

    def test_invalid_link_fails_before_create(self) -> None:
        with patch.object(runner, "fields_by_name", return_value={"飞书文档": {"type": 1}, "飞书文档链接": {"type": 15}}), \
                patch.object(runner.feishu, "request_json") as request_json:
            with self.assertRaisesRegex(ValueError, "valid http"):
                runner.create_script_package_record(
                    "token", "app", "table", {"飞书文档": "javascript:alert(1)"}
                )
        request_json.assert_not_called()

    def test_wrong_link_field_shape_fails_before_create(self) -> None:
        with patch.object(runner, "fields_by_name", return_value={"飞书文档": {"type": 15}}), \
                patch.object(runner.feishu, "request_json") as request_json:
            with self.assertRaisesRegex(ValueError, "URL string"):
                runner.create_script_package_record(
                    "token", "app", "table", {"飞书文档": {"link": "https://my.feishu.cn/docx/doc_test"}}
                )
        request_json.assert_not_called()

    def test_production_failure_fixture_serializes_document_link_object(self) -> None:
        fields = runner.format_script_package_record_fields(
            {"飞书文档": "https://my.feishu.cn/docx/doc_generated", "飞书文档链接": "https://my.feishu.cn/docx/doc_generated"},
            {"飞书文档": {"type": 1}, "飞书文档链接": {"type": 15}},
        )

        self.assertIsInstance(fields["飞书文档链接"], dict)
        self.assertEqual(fields["飞书文档链接"]["link"], "https://my.feishu.cn/docx/doc_generated")

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


if __name__ == "__main__":
    unittest.main()
