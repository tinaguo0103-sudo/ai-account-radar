#!/usr/bin/env python3
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import content_sampler
import push_today10_to_feishu


def sample_item(title: str, url: str, fingerprint: str) -> content_sampler.ContentItem:
    return content_sampler.ContentItem(
        source_type="公众号文章",
        platform="微信公众号",
        account_name="示例账号",
        title=title,
        url=url,
        content_shape="长文",
        cover_text="",
        body_snippet="正文",
        published_at="2026-07-04",
        comment_questions="",
        ocr_text="",
        fetch_method="recovered_from_run_csv",
        fetch_status="ok",
        failure_reason="",
        fingerprint=fingerprint,
    )


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

    def test_update_record_fields_does_not_blind_retry_timeout(self) -> None:
        calls = {"count": 0}
        original_request_json = content_sampler.feishu.request_json
        original_sleep = content_sampler.time.sleep

        def flaky_request_json(*args, **kwargs):
            calls["count"] += 1
            raise TimeoutError("The read operation timed out")

        try:
            content_sampler.feishu.request_json = flaky_request_json
            content_sampler.time.sleep = lambda *_args, **_kwargs: None
            with self.assertRaises(TimeoutError):
                content_sampler.update_record_fields("token", "app", "table", "rec", {"运行批次": "run"})
        finally:
            content_sampler.feishu.request_json = original_request_json
            content_sampler.time.sleep = original_sleep

        self.assertEqual(calls["count"], 1)

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

    def test_recovery_plan_counts_existing_run_and_missing_records(self) -> None:
        items = [
            sample_item("已有本批次", "https://example.com/a", "fp-a"),
            sample_item("历史重复", "https://example.com/b", "fp-b"),
            sample_item("待新增", "https://example.com/c", "fp-c"),
        ]
        records = [
            {"fields": {"内容指纹": "fp-a", "最近参与运行批次": "run_20260704_080730"}},
            {"fields": {"内容指纹": "fp-b", "最近参与运行批次": "run_old"}},
        ]
        original_all_records = content_sampler.all_records
        try:
            content_sampler.all_records = lambda *_args, **_kwargs: records
            plan = content_sampler.content_inbox_recovery_plan("token", "app", "table", items, "run_20260704_080730")
        finally:
            content_sampler.all_records = original_all_records

        self.assertEqual(plan["total_items"], 3)
        self.assertEqual(plan["existing_matches"], 2)
        self.assertEqual(plan["already_in_run"], 1)
        self.assertEqual(plan["records_to_update"], 2)
        self.assertEqual(plan["queued_create"], 1)

    def test_recovery_log_mirrors_write_feishu_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            run_dir = tmp_root / "run_20260704_080730"
            run_dir.mkdir()
            original_out = content_sampler.OUT
            original_latest_write = content_sampler.LATEST_WRITE_DIR
            try:
                content_sampler.OUT = tmp_root / "output"
                content_sampler.LATEST_WRITE_DIR = tmp_root / "output" / "latest_write"
                content_sampler.write_recovery_sampler_log(
                    run_dir,
                    "run_20260704_080730",
                    "pending",
                    3,
                    {"total_items": 3, "processed_items": 0, "remaining_items": 3},
                )
            finally:
                content_sampler.OUT = original_out
                content_sampler.LATEST_WRITE_DIR = original_latest_write

            self.assertTrue((run_dir / "content_sampler_log.json").exists())
            self.assertFalse((tmp_root / "output" / "content_sampler_log.json").exists())
            self.assertTrue((tmp_root / "output" / "latest_write" / "content_sampler_log.json").exists())

    def test_feishu_visible_rows_infers_missing_level_for_publishable_script_candidates(self) -> None:
        rows = [
            {
                "strict_fail_closed": "true",
                "我的选题标题": "值得推进",
                "选题命题": "值得推进的测试标题",
                "推荐动作": "生成脚本包",
                "是否建议进入制作": "是",
                "今日建议级别": "",
                "我要做的实验": "拿一条真实材料跑一轮脚本包生成并记录结果",
                "验证方式": "输入真实材料，检查是否能输出脚本包草稿和通过/失败记录",
                "title_permission": "可发布标题",
                "可发布标题": "值得推进的测试标题",
                "主编判断摘要": "来源证据来自真实材料，我会接到 Austin 的脚本包恢复链路；但仍要记录恢复边界。",
                "标题思路": "标题先写恢复链路的真实动作，不写成泛工具教程。",
            },
            {
                "strict_fail_closed": "true",
                "我的选题标题": "先观察",
                "推荐动作": "暂存观察",
                "是否建议进入制作": "暂存观察",
                "今日建议级别": "",
                "我要做的实验": "拿一条真实材料跑一轮脚本包生成并记录结果",
                "验证方式": "输入真实材料，检查是否能输出脚本包草稿和通过/失败记录",
                "title_permission": "内部测试标题",
            },
        ]

        visible, omitted = push_today10_to_feishu.feishu_visible_rows(rows)

        self.assertEqual(len(visible), 1)
        self.assertEqual(omitted, 1)
        self.assertEqual(visible[0]["今日建议级别"], "推荐制作")

    def test_feishu_visible_rows_omits_internal_title_script_candidates(self) -> None:
        rows = [
            {
                "strict_fail_closed": "true",
                "我的选题标题": "内部测试标题候选",
                "推荐动作": "生成脚本包",
                "是否建议进入制作": "是",
                "今日建议级别": "",
                "我要做的实验": "拿一条真实材料跑一轮脚本包生成并记录结果",
                "验证方式": "输入真实材料，检查是否能输出脚本包草稿和通过/失败记录",
                "title_permission": "内部测试标题",
                "主编判断摘要": "来源证据来自真实材料，我会接到 Austin 的脚本包恢复链路；但标题仍缺发布表达。",
                "标题思路": "还只是内部测试标题，不能进入生成脚本包列表。",
            },
        ]

        visible, omitted = push_today10_to_feishu.feishu_visible_rows(rows)

        self.assertEqual(visible, [])
        self.assertEqual(omitted, 1)

    def test_legacy_script_candidate_gets_executable_experiment(self) -> None:
        row = {
            "我的选题标题": "Prompt退环境后，真正要学的是把AI任务跑成闭环",
            "选题命题": "Prompt退环境后，真正要学的是把AI任务跑成闭环",
            "推荐动作": "生成脚本包",
            "是否建议进入制作": "是",
            "今日建议级别": "",
            "来源内容": "Prompt退环境",
            "业务场景": "AI项目生产环境",
            "旧流程痛点": "只有工具调用，没有阶段交付和失败回滚",
            "可展示结果": "一页项目检查表",
            "可沉淀资产": "项目验收清单",
            "我要做的实验": "",
            "验证方式": "",
        }

        self.assertNotEqual(push_today10_to_feishu.experiment_for(row), push_today10_to_feishu.FALLBACK_EXPERIMENT_PROMPT)
        self.assertIn("输入", push_today10_to_feishu.experiment_for(row))
        self.assertIn("记录输出物", push_today10_to_feishu.validation_for(row))
        self.assertEqual(push_today10_to_feishu.display_title_for(row), row["选题命题"])


if __name__ == "__main__":
    unittest.main()
