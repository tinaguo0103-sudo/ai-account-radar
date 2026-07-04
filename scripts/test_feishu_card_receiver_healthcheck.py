#!/usr/bin/env python3
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import check_feishu_card_cloud_receiver as health


class FeishuCardReceiverHealthcheckTest(unittest.TestCase):
    def test_explicit_staging_topic_table_id_takes_priority_over_table_name_lookup(self) -> None:
        captured: dict[str, str] = {}

        def fake_request_json(method: str, path: str, *, token: str) -> dict[str, object]:
            captured["method"] = method
            captured["path"] = path
            captured["token"] = token
            return {"data": {"items": [{"record_id": "rec_test"}], "has_more": False}}

        with patch.dict(os.environ, {
            "FEISHU_BASE_APP_TOKEN": "base_test",
            "FEISHU_TOPIC_DECISION_TABLE_ID": "tbl_staging_topic",
        }, clear=True), \
            patch.object(health.feishu, "tenant_token", return_value="tenant_test"), \
            patch.object(health.feishu, "list_tables", side_effect=AssertionError("list_tables should not be called")), \
            patch.object(health.feishu, "request_json", side_effect=fake_request_json):
            result = health.check_feishu_read("topic_decision")

        self.assertTrue(result["ok"])
        self.assertEqual(result["table_id"], "tbl_staging_topic")
        self.assertEqual(result["table_id_source"], "FEISHU_TOPIC_DECISION_TABLE_ID")
        self.assertIn("/tables/tbl_staging_topic/records?", captured["path"])

    def test_table_name_lookup_is_used_only_without_explicit_table_id(self) -> None:
        with patch.dict(os.environ, {"FEISHU_BASE_APP_TOKEN": "base_test"}, clear=True), \
            patch.object(health.feishu, "tenant_token", return_value="tenant_test"), \
            patch.object(health.feishu, "list_tables", return_value=[{"name": "04 分析与选题", "table_id": "tbl_by_name"}]), \
            patch.object(health.feishu, "request_json", return_value={"data": {"items": [], "has_more": False}}):
            result = health.check_feishu_read("topic_decision")

        self.assertTrue(result["ok"])
        self.assertEqual(result["table_id"], "tbl_by_name")
        self.assertEqual(result["table_id_source"], "name:04 分析与选题")

    def test_require_test_card_config_reports_missing_isolation_inputs(self) -> None:
        with patch.dict(os.environ, {
            "AI_ACCOUNT_RADAR_ENV": "staging",
            "FEISHU_TOPIC_TABLE_ID": "tbl_staging_topic",
        }, clear=True):
            result = health.check_test_card_config("", "topic_decision")

        self.assertFalse(result["ok"])
        self.assertIn("FEISHU_TENCENT_SCF_URL or --url", result["missing"])
        self.assertIn("FEISHU_CARD_RECEIVE_TARGETS", result["missing"])
        self.assertIn("FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS", result["missing"])
        self.assertEqual(result["table_id"], "tbl_staging_topic")
        self.assertEqual(result["table_id_source"], "FEISHU_TOPIC_TABLE_ID")

    def test_require_test_card_config_accepts_isolated_staging_inputs(self) -> None:
        with patch.dict(os.environ, {
            "AI_ACCOUNT_RADAR_ENV": "staging",
            "FEISHU_TOPIC_TABLE_ID": "tbl_staging_topic",
            "FEISHU_CARD_RECEIVE_TARGETS": "open_id:ou_test",
            "FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS": "open_id:ou_test_direction",
        }, clear=True):
            result = health.check_test_card_config("https://example.test/scf", "topic_decision")

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["table_id"], "tbl_staging_topic")
        self.assertTrue(result["card_receive_targets_configured"])
        self.assertTrue(result["production_direction_receive_targets_configured"])

    def test_require_test_card_config_rejects_production_environment(self) -> None:
        with patch.dict(os.environ, {
            "AI_ACCOUNT_RADAR_ENV": "production",
            "FEISHU_TOPIC_TABLE_ID": "tbl_prod_topic",
            "FEISHU_CARD_RECEIVE_TARGETS": "chat_id:oc_prod",
            "FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS": "chat_id:oc_prod",
        }, clear=True):
            result = health.check_test_card_config("https://example.test/scf", "topic_decision")

        self.assertFalse(result["ok"])
        self.assertIn("non-production AI_ACCOUNT_RADAR_ENV", result["missing"])


if __name__ == "__main__":
    unittest.main()
