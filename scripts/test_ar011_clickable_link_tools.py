#!/usr/bin/env python3
from __future__ import annotations

import unittest

import backfill_script_package_clickable_links as backfill
import script_package_clickable_link_flow_qa as flow_qa
import setup_script_package_clickable_links as setup_links


class SetupScriptPackageClickableLinksTest(unittest.TestCase):
    def test_plan_url_fields_detects_create_ok_and_conflict(self) -> None:
        plan = setup_links.plan_url_fields({
            "飞书文档链接": {"type": 15},
            "飞书文件夹链接": {"type": 1},
        })

        self.assertEqual(plan["already_ok"], ["飞书文档链接"])
        self.assertEqual(plan["create"], [])
        self.assertEqual(plan["conflicts"], [{"field_name": "飞书文件夹链接", "current_type": 1, "required_type": 15}])

    def test_view_patch_plan_keeps_scope_to_selected_view(self) -> None:
        fields = {
            "脚本标题": {"field_id": "fld_title", "is_primary": True},
            "飞书文档链接": {"field_id": "fld_doc"},
            "飞书文件夹链接": {"field_id": "fld_folder"},
            "内部字段": {"field_id": "fld_internal"},
        }
        plan = setup_links.plan_view_patch(
            "脚本包主视图",
            fields,
            {"view_id": "view1", "property": {"hidden_fields": ["fld_doc", "fld_internal"]}},
        )

        self.assertEqual(plan["view_id"], "view1")
        self.assertFalse(plan["will_create_view"])
        self.assertIn("飞书文档链接", plan["ensured_visible_fields"])
        self.assertEqual(plan["property"]["hidden_fields"], ["fld_internal"])

    def test_new_view_uses_minimal_visible_fields(self) -> None:
        fields = {
            "脚本标题": {"field_id": "fld_title", "is_primary": True},
            "飞书文档链接": {"field_id": "fld_doc"},
            "飞书文件夹链接": {"field_id": "fld_folder"},
            "内部字段": {"field_id": "fld_internal"},
        }
        plan = setup_links.plan_view_patch("AR-011 L3 链接验证", fields, None)

        self.assertTrue(plan["will_create_view"])
        self.assertEqual(plan["property"]["hidden_fields"], ["fld_internal"])
        self.assertIn("飞书文件夹链接", plan["new_view_default_visible_fields"])


class BackfillScriptPackageClickableLinksTest(unittest.TestCase):
    def test_build_record_plan_updates_only_missing_url_fields(self) -> None:
        plan = backfill.build_record_plan({
            "record_id": "rec1",
            "fields": {
                "脚本标题": "测试",
                "飞书文档": "https://my.feishu.cn/docx/doc1",
                "飞书文件夹": "https://my.feishu.cn/drive/folder/folder1",
            },
        })

        self.assertEqual(plan["status"], "to_update")
        self.assertEqual(set(plan["updates"]), {"飞书文档链接", "飞书文件夹链接"})
        self.assertEqual(plan["updates"]["飞书文档链接"], {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/doc1"})

    def test_build_record_plan_is_idempotent_when_links_match(self) -> None:
        plan = backfill.build_record_plan({
            "record_id": "rec1",
            "fields": {
                "飞书文档": "https://my.feishu.cn/docx/doc1",
                "飞书文档链接": {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/doc1"},
            },
        })

        self.assertEqual(plan["status"], "already_ok")
        self.assertEqual(plan["updates"], {})
        self.assertEqual(plan["already_ok"], ["飞书文档链接"])

    def test_build_record_plan_flags_conflict_without_overwriting(self) -> None:
        plan = backfill.build_record_plan({
            "record_id": "rec1",
            "fields": {
                "飞书文档": "https://my.feishu.cn/docx/new",
                "飞书文档链接": {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/old"},
            },
        })

        self.assertEqual(plan["status"], "conflict")
        self.assertEqual(plan["updates"], {})
        self.assertIn("飞书文档链接", plan["conflicts"])

    def test_build_backfill_plan_filters_record_ids_and_counts(self) -> None:
        plan = backfill.build_backfill_plan(
            [
                {"record_id": "rec1", "fields": {"飞书文档": "https://my.feishu.cn/docx/doc1"}},
                {"record_id": "rec2", "fields": {"飞书文档": ""}},
            ],
            record_ids={"rec1"},
        )

        self.assertEqual(plan["total_records"], 2)
        self.assertEqual(plan["selected_records"], 1)
        self.assertEqual(plan["counts"], {"to_update": 1})
        self.assertEqual(plan["to_update_record_ids"], ["rec1"])

    def test_read_back_status_reports_mismatch(self) -> None:
        status = backfill.read_back_status(
            {"fields": {"飞书文档链接": {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/actual"}}},
            {"record_id": "rec1", "updates": {"飞书文档链接": {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/expected"}}},
        )

        self.assertFalse(status["ok"])
        self.assertEqual(status["mismatches"][0]["field"], "飞书文档链接")

    def test_field_type_errors_require_url_fields(self) -> None:
        errors = backfill.field_type_errors({
            "飞书文档链接": {"type": 15},
            "飞书文件夹链接": {"type": 1},
        })

        self.assertEqual(errors, [{"field": "飞书文件夹链接", "reason": "wrong_type", "type": 1}])


class ScriptPackageClickableLinkFlowQATest(unittest.TestCase):
    def test_verify_record_requires_legacy_and_url_fields(self) -> None:
        result = flow_qa.verify_record(
            {
                "fields": {
                    "飞书文档": "https://my.feishu.cn/docx/doc",
                    "飞书文件夹": "https://my.feishu.cn/drive/folder/folder",
                    "飞书文档链接": {"text": "打开飞书文档", "link": "https://my.feishu.cn/docx/doc"},
                    "飞书文件夹链接": {"text": "打开飞书文件夹", "link": "https://my.feishu.cn/drive/folder/folder"},
                }
            },
            doc_url="https://my.feishu.cn/docx/doc",
            folder_url="https://my.feishu.cn/drive/folder/folder",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(all(result["checks"].values()))

    def test_verify_record_fails_when_url_mirror_missing(self) -> None:
        result = flow_qa.verify_record(
            {"fields": {"飞书文档": "https://my.feishu.cn/docx/doc"}},
            doc_url="https://my.feishu.cn/docx/doc",
            folder_url="https://my.feishu.cn/drive/folder/folder",
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["folder_url_field"])


if __name__ == "__main__":
    unittest.main()
