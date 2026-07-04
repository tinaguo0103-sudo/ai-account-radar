#!/usr/bin/env python3
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import content_sampler


class ContentSamplerRecoveryTest(unittest.TestCase):
    def test_content_item_from_row_preserves_run_csv_fields(self) -> None:
        row = {
            "来源类型": "competitor_article",
            "平台": "微信公众号",
            "账号名/公众号名": "示例账号",
            "内容标题": "示例标题",
            "内容链接": "https://example.com/post",
            "内容形态": "长文",
            "正文/字幕/简介片段": "正文内容",
            "发布时间": "2026-07-04",
            "抓取方式": "wechat_public_html_js_content",
            "抓取状态": "success",
            "内容指纹": "fp-1",
            "正文长度": "1234",
            "是否全文解析": "是",
            "原始payload路径": "/tmp/payload.txt",
            "是否来自已解析URL复用": "是",
        }

        item = content_sampler.content_item_from_row(row)

        self.assertEqual(item.source_type, "公众号文章")
        self.assertEqual(item.title, "示例标题")
        self.assertEqual(item.fingerprint, "fp-1")
        self.assertEqual(item.raw_text_length, 1234)
        self.assertEqual(item.ocr_text, "/tmp/payload.txt")
        self.assertEqual(item.reused_url, "是")

    def test_update_record_fields_retries_transient_timeout(self) -> None:
        calls = {"count": 0}
        original_request_json = content_sampler.feishu.request_json
        original_sleep = content_sampler.time.sleep

        def flaky_request_json(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("The read operation timed out")
            return {"code": 0}

        try:
            content_sampler.feishu.request_json = flaky_request_json
            content_sampler.time.sleep = lambda *_args, **_kwargs: None
            content_sampler.update_record_fields("token", "app", "table", "rec", {"运行批次": "run"})
        finally:
            content_sampler.feishu.request_json = original_request_json
            content_sampler.time.sleep = original_sleep

        self.assertEqual(calls["count"], 2)

    def test_recover_dry_run_reads_existing_run_without_feishu_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_20260704_080730"
            run_dir.mkdir()
            with (run_dir / "content_items.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["内容标题", "内容链接", "内容指纹", "正文/字幕/简介片段"])
                writer.writeheader()
                writer.writerow({
                    "内容标题": "测试标题",
                    "内容链接": "https://example.com/a",
                    "内容指纹": "fp-a",
                    "正文/字幕/简介片段": "测试正文",
                })

            result = content_sampler.recover_content_inbox_from_run(run_dir, "run_20260704_080730", write_feishu=False)

            self.assertEqual(result["content_items"], 1)
            self.assertFalse(result["will_write_feishu"])
            self.assertFalse((run_dir / "content_sampler_log.json").exists())


if __name__ == "__main__":
    unittest.main()
