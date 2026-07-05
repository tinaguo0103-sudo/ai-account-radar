#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import script_package_shared as shared


READY_RECORD = {
    "record_id": "rec_ready",
    "fields": {
        "状态": "生成脚本包",
        "制作方向卡状态": "已提交",
        "是否已生成脚本稿": "否",
    },
}


class ScriptPackageSharedTableResolutionTest(unittest.TestCase):
    def test_ready_topics_prefers_explicit_staging_topic_table_id(self) -> None:
        with patch.dict(
            os.environ,
            {
                "FEISHU_TOPIC_TABLE_ID": "tbl_staging_topic",
                "FEISHU_SCRIPT_PACKAGE_TABLE_ID": "tbl_staging_script",
            },
            clear=True,
        ), patch.object(shared.feishu, "list_tables", return_value=[]), patch.object(shared, "all_records", return_value=[READY_RECORD]) as all_records:
            table_ids, ready = shared.feishu_ready_topics("tenant_token", "app_token")

        self.assertEqual(table_ids["topic_decision"], "tbl_staging_topic")
        self.assertEqual(table_ids["script_package"], "tbl_staging_script")
        all_records.assert_called_once_with("tenant_token", "app_token", "tbl_staging_topic")
        self.assertEqual([record["record_id"] for record in ready], ["rec_ready"])

    def test_ready_topics_keeps_name_fallback_without_explicit_topic_table_id(self) -> None:
        tables = [
            {"name": "04 分析与选题", "table_id": "tbl_named_topic"},
            {"name": "06 完整脚本与制作包", "table_id": "tbl_named_script"},
        ]
        with patch.dict(os.environ, {}, clear=True), patch.object(shared.feishu, "list_tables", return_value=tables), patch.object(shared, "all_records", return_value=[READY_RECORD]) as all_records:
            table_ids, ready = shared.feishu_ready_topics("tenant_token", "app_token")

        self.assertEqual(table_ids["topic_decision"], "tbl_named_topic")
        self.assertEqual(table_ids["script_package"], "tbl_named_script")
        all_records.assert_called_once_with("tenant_token", "app_token", "tbl_named_topic")
        self.assertEqual([record["record_id"] for record in ready], ["rec_ready"])


if __name__ == "__main__":
    unittest.main()
