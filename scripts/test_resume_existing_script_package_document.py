#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import resume_existing_script_package_document as resume
from codex_script_package_runner import FeishuDocSyncResult


SOURCE_ID = "recvpBSLojkI8f"
SCRIPT_ID = "recvpCxiSo2M5A"
TITLE = "你的知识库不会自动成长，它只会自动堆满垃圾"
RUN_ID = "run_20260717_093104"


def source_record(**fields):
    values = {
        "我的选题标题": TITLE,
        "运行批次": RUN_ID,
        "状态": "生成脚本包",
        "是否已生成脚本稿": "是",
    }
    values.update(fields)
    return {"record_id": SOURCE_ID, "fields": values}


def script_record(**fields):
    values = {
        "脚本标题": TITLE,
        "关联选题": TITLE,
        "文档同步状态": resume.FAILED_STATUS,
        "文档同步错误": "invalid_grant code 20037",
        "飞书文档": "",
        "飞书文档链接": None,
        "飞书文件夹": "https://my.feishu.cn/drive/folder/folder_test",
        "飞书文件夹链接": {"text": "打开飞书文件夹", "link": "https://my.feishu.cn/drive/folder/folder_test"},
        "QA结果": "revise",
    }
    values.update(fields)
    return {"record_id": SCRIPT_ID, "fields": values}


def metadata():
    return {
        "飞书文档": {"type": 1},
        "飞书文档链接": {"type": 15},
        "飞书文件夹": {"type": 1},
        "飞书文件夹链接": {"type": 15},
        "文档同步状态": {"type": 1},
        "文档同步错误": {"type": 1},
    }


class ResumeExistingScriptPackageDocumentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.markdown = self.root / "package.md"
        self.markdown.write_text(f"# {TITLE}\n\nbody\n", encoding="utf-8")
        self.sha = hashlib.sha256(self.markdown.read_bytes()).hexdigest()
        self.args = argparse.Namespace(
            source_04_record_id=SOURCE_ID,
            existing_06_record_id=SCRIPT_ID,
            markdown_path=str(self.markdown),
            expected_sha256=self.sha,
            check_only=True,
            write=False,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_with_fixture(self, scripts=None, **extra_patches):
        scripts = scripts or [script_record()]
        patches = {
            "load_local_env": patch.object(resume, "load_local_env"),
            "tenant_token": patch.object(resume.feishu, "tenant_token", return_value="tenant"),
            "tables": patch.object(resume, "resolve_script_package_table_ids", return_value={"topic_decision": "04", "script_package": "06"}),
            "records": patch.object(resume, "all_records", side_effect=[[source_record()], scripts]),
            "metadata": patch.object(resume, "fields_by_name", return_value=metadata()),
        }
        patches.update(extra_patches)
        entered = [item.start() for item in patches.values()]
        self.addCleanup(lambda: [item.stop() for item in patches.values()])
        with patch.dict("os.environ", {"FEISHU_BASE_APP_TOKEN": "app", "SCRIPT_PACKAGE_DOC_RESUME_STATE_ROOT": str(self.root / "state")}, clear=False):
            return resume.run(self.args), dict(zip(patches, entered))

    def test_production_partial_fixture_check_only_has_zero_side_effects(self) -> None:
        with patch.object(resume, "oauth_user_token") as oauth, \
                patch.object(resume, "create_feishu_document") as create_doc, \
                patch.object(resume, "update_existing_record") as update:
            result, _ = self.run_with_fixture()

        self.assertTrue(result["ok"])
        self.assertTrue(result["would_create_document"])
        self.assertTrue(result["would_update_existing_06"])
        self.assertEqual(result["business_writes"], 0)
        self.assertEqual(result["codex_calls"], 0)
        self.assertEqual(result["script_06_create_calls"], 0)
        self.assertEqual(result["source_04_update_calls"], 0)
        self.assertEqual(result["queue_actions"], 0)
        oauth.assert_not_called(); create_doc.assert_not_called(); update.assert_not_called()

    def test_write_creates_one_document_and_updates_existing_record(self) -> None:
        self.args.check_only = False; self.args.write = True
        green = script_record(**{
            "文档同步状态": "已创建飞书文档并同步",
            "飞书文档": "https://my.feishu.cn/docx/doc_test",
            "飞书文档链接": {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/doc_test"},
        })
        with patch.object(resume, "oauth_user_token", return_value="user") as oauth, \
                patch.object(resume, "create_feishu_document", return_value=FeishuDocSyncResult(url="https://my.feishu.cn/docx/doc_test", status="已创建飞书文档并同步")) as create_doc, \
                patch.object(resume, "update_existing_record", return_value=1) as update, \
                patch.object(resume, "read_script_record", return_value=green):
            result, _ = self.run_with_fixture()

        self.assertEqual(result["document_creates"], 1)
        self.assertEqual(result["existing_06_updates"], 1)
        self.assertEqual(result["script_06_create_calls"], 0)
        oauth.assert_called_once(); create_doc.assert_called_once(); update.assert_called_once()
        sent_fields = update.call_args.args[4]
        self.assertEqual(sent_fields["飞书文档链接"]["link"], "https://my.feishu.cn/docx/doc_test")
        self.assertEqual(sent_fields["文档同步错误"], "")

    def test_invalid_oauth_blocks_document_and_record_writes(self) -> None:
        self.args.check_only = False; self.args.write = True
        with patch.object(resume, "oauth_user_token", side_effect=resume.ResumeError("user_oauth_validation_failed")), \
                patch.object(resume, "create_feishu_document") as create_doc, \
                patch.object(resume, "update_existing_record") as update:
            with self.assertRaisesRegex(resume.ResumeError, "oauth"):
                self.run_with_fixture()
        create_doc.assert_not_called(); update.assert_not_called()

    def test_existing_green_record_is_idempotent(self) -> None:
        self.args.check_only = False; self.args.write = True
        green = script_record(**{
            "文档同步状态": "已创建飞书文档并同步",
            "飞书文档": "https://my.feishu.cn/docx/doc_test",
            "飞书文档链接": {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/doc_test"},
        })
        with patch.object(resume, "oauth_user_token") as oauth, patch.object(resume, "create_feishu_document") as create_doc:
            result, _ = self.run_with_fixture(scripts=[green])
        self.assertTrue(result["idempotent"])
        self.assertEqual(result["business_writes"], 0)
        oauth.assert_not_called(); create_doc.assert_not_called()

    def test_recovery_state_reuses_document_without_second_create(self) -> None:
        self.args.check_only = False; self.args.write = True
        state_path = resume.recovery_state_path(self.root / "state", SCRIPT_ID)
        resume.write_state(state_path, {
            "source_04_record_id": SOURCE_ID, "existing_06_record_id": SCRIPT_ID,
            "markdown_path": str(self.markdown.absolute()), "markdown_sha256": self.sha,
            "run_id": RUN_ID, "document_url": "https://my.feishu.cn/docx/doc_saved",
        })
        green = script_record(**{
            "文档同步状态": "已创建飞书文档并同步",
            "飞书文档": "https://my.feishu.cn/docx/doc_saved",
            "飞书文档链接": {"link": "https://my.feishu.cn/docx/doc_saved", "text": "打开飞书文档"},
        })
        with patch.object(resume, "oauth_user_token", return_value="user"), \
                patch.object(resume, "create_feishu_document") as create_doc, \
                patch.object(resume, "update_existing_record", return_value=1), \
                patch.object(resume, "read_script_record", return_value=green):
            result, _ = self.run_with_fixture()
        self.assertEqual(result["document_creates"], 0)
        create_doc.assert_not_called()
        self.assertFalse(state_path.exists())

    def test_document_created_update_failed_keeps_recovery_state(self) -> None:
        self.args.check_only = False; self.args.write = True
        with patch.object(resume, "oauth_user_token", return_value="user"), \
                patch.object(resume, "create_feishu_document", return_value=FeishuDocSyncResult(url="https://my.feishu.cn/docx/doc_saved", status="已创建飞书文档并同步")), \
                patch.object(resume, "update_existing_record", side_effect=resume.ResumeError("existing_06_update_failed")):
            with self.assertRaisesRegex(resume.ResumeError, "update_failed"):
                self.run_with_fixture()
        state = json.loads(resume.recovery_state_path(self.root / "state", SCRIPT_ID).read_text())
        self.assertEqual(state["document_url"], "https://my.feishu.cn/docx/doc_saved")

    def test_update_timeout_after_commit_is_read_back_success(self) -> None:
        expected_url = "https://my.feishu.cn/docx/doc_test"
        with patch.object(resume.feishu, "request_json", side_effect=TimeoutError("unknown")) as put, \
                patch.object(resume, "read_script_record", return_value=script_record(**{
                    "文档同步状态": "已创建飞书文档并同步",
                    "飞书文档链接": {"link": expected_url},
                })):
            attempts = resume.update_existing_record("tenant", "app", "06", SCRIPT_ID, {}, expected_url)
        self.assertEqual(attempts, 1); self.assertEqual(put.call_count, 1)

    def test_update_timeout_before_commit_retries_bounded(self) -> None:
        expected_url = "https://my.feishu.cn/docx/doc_test"
        with patch.object(resume.feishu, "request_json", side_effect=[TimeoutError("unknown"), {"data": {}}]) as put, \
                patch.object(resume, "read_script_record", return_value=script_record()):
            attempts = resume.update_existing_record("tenant", "app", "06", SCRIPT_ID, {}, expected_url)
        self.assertEqual(attempts, 2); self.assertEqual(put.call_count, 2)

    def test_identity_and_metadata_mutations_fail_closed(self) -> None:
        cases = {
            "absent": ([], [script_record()], metadata(), self.markdown, self.sha),
            "duplicate": ([source_record()], [script_record(), {**script_record(), "record_id": "recDuplicate"}], metadata(), self.markdown, self.sha),
            "wrong_title": ([source_record()], [script_record(**{"脚本标题": "other", "关联选题": "other"})], metadata(), self.markdown, self.sha),
            "bad_sha": ([source_record()], [script_record()], metadata(), self.markdown, "0" * 64),
            "missing_doc_meta": ([source_record()], [script_record()], {k: v for k, v in metadata().items() if k != "飞书文档链接"}, self.markdown, self.sha),
            "wrong_folder_meta": ([source_record()], [script_record()], {**metadata(), "飞书文件夹链接": {"type": 1}}, self.markdown, self.sha),
            "conflicting_doc": ([source_record()], [script_record(**{"飞书文档": "https://my.feishu.cn/docx/conflict"})], metadata(), self.markdown, self.sha),
        }
        for name, values in cases.items():
            with self.subTest(name=name), self.assertRaises(Exception):
                resume.validate_records(SOURCE_ID, SCRIPT_ID, *values)

    def test_wrong_source_run_and_not_generated_are_blocked(self) -> None:
        for source in [source_record(**{"运行批次": "other"}), source_record(**{"是否已生成脚本稿": "否"})]:
            with self.assertRaises(resume.ResumeError):
                resume.validate_records(SOURCE_ID, SCRIPT_ID, [source], [script_record()], metadata(), self.markdown, self.sha)


if __name__ == "__main__":
    unittest.main()
