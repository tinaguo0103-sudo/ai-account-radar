#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

import feishu_schema_cleanup_audit as audit


class FeishuSchemaCleanupAuditTests(unittest.TestCase):
    def test_referenced_and_keep_fields_are_not_delete_candidates(self) -> None:
        schema = {
            "source_sampling": [
                {"field_name": "名称", "type": 1},
                {"field_name": "栏目权重", "type": 1},
            ],
            "content_inbox": [
                {"field_name": "标题", "type": 1},
                {"field_name": "已废弃字段", "type": 1},
            ],
            "topic_decision": [
                {"field_name": "选题标题", "type": 1},
                {"field_name": "无引用临时字段", "type": 1},
            ],
        }

        with patch.object(audit, "repo_texts", return_value={"scripts/example.py": "名称 标题 选题标题"}):
            report = audit.audit_schema(schema)

        source_fields = {row["field_name"]: row for row in report["tables"]["source_sampling"]["fields"]}
        content_fields = {row["field_name"]: row for row in report["tables"]["content_inbox"]["fields"]}
        topic_fields = {row["field_name"]: row for row in report["tables"]["topic_decision"]["fields"]}

        self.assertEqual(source_fields["名称"]["recommendation"], "keep")
        self.assertEqual(content_fields["标题"]["recommendation"], "keep")
        self.assertEqual(topic_fields["选题标题"]["recommendation"], "keep")
        self.assertEqual(content_fields["已废弃字段"]["recommendation"], "delete_candidate")
        self.assertEqual(topic_fields["无引用临时字段"]["recommendation"], "delete_candidate")
        self.assertTrue(report["dry_run_only"])

    def test_select_options_get_reference_matrix(self) -> None:
        schema = {
            "topic_decision": [
                {
                    "field_name": "选择原因标签",
                    "type": 4,
                    "property": {
                        "options": [
                            {"name": "证据够"},
                            {"name": "业务不存在的临时标签"},
                        ]
                    },
                }
            ],
            "source_sampling": [],
            "content_inbox": [],
        }

        with patch.object(audit, "repo_texts", return_value={"scripts/example.py": "选择原因标签 证据够"}):
            report = audit.audit_schema(schema)
        field = report["tables"]["topic_decision"]["fields"][0]
        options = {option["name"]: option for option in field["options"]}

        self.assertEqual(field["recommendation"], "keep")
        self.assertGreater(options["证据够"]["reference_count"], 0)
        self.assertEqual(options["业务不存在的临时标签"]["recommendation"], "delete_candidate")

    def test_fill_rate_option_usage_and_views_make_audit_actionable(self) -> None:
        schema = {
            "source_sampling": [],
            "content_inbox": [],
            "topic_decision": {
                "fields": [
                    {"field_name": "无引用但有值", "field_id": "fld_a", "type": 1},
                    {
                        "field_name": "临时标签",
                        "field_id": "fld_b",
                        "type": 4,
                        "property": {"options": [{"name": "仍在使用"}, {"name": "空置选项"}]},
                    },
                ],
                "records": [
                    {"fields": {"无引用但有值": "真实业务值", "临时标签": "仍在使用"}},
                    {"fields": {"无引用但有值": "", "临时标签": ""}},
                ],
                "views": [
                    {
                        "view_name": "主视图",
                        "view_id": "vew_1",
                        "view_type": "grid",
                        "property": {"hidden_fields": ["fld_b"]},
                    }
                ],
            },
        }

        with patch.object(audit, "repo_texts", return_value={}):
            report = audit.audit_schema(schema)

        table = report["tables"]["topic_decision"]
        fields = {row["field_name"]: row for row in table["fields"]}
        options = {row["name"]: row for row in fields["临时标签"]["options"]}

        self.assertEqual(fields["无引用但有值"]["recommendation"], "needs_manual_review")
        self.assertEqual(fields["无引用但有值"]["fill_count"], 1)
        self.assertEqual(options["仍在使用"]["recommendation"], "needs_manual_review")
        self.assertEqual(options["空置选项"]["recommendation"], "delete_candidate")
        self.assertEqual(table["view_count"], 1)
        self.assertEqual(table["views"][0]["hidden_fields_sample"], ["临时标签"])
        self.assertTrue(any(item["kind"] == "field" for item in table["cleanup_matrix"]))
        self.assertTrue(any(item["kind"] == "option" for item in table["cleanup_matrix"]))


if __name__ == "__main__":
    unittest.main()
