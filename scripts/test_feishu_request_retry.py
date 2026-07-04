#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import feishu_automation_notify
import push_to_feishu


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FeishuRequestRetryTest(unittest.TestCase):
    def test_get_retries_transient_timeout(self) -> None:
        calls = {"count": 0}
        original_urlopen = push_to_feishu.urlopen
        original_sleep = push_to_feishu.time.sleep

        def flaky_urlopen(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("The read operation timed out")
            return FakeResponse({"code": 0, "data": {"ok": True}})

        try:
            push_to_feishu.urlopen = flaky_urlopen
            push_to_feishu.time.sleep = lambda *_args, **_kwargs: None
            result = push_to_feishu.request_json("GET", "/bitable/v1/apps/app/tables")
        finally:
            push_to_feishu.urlopen = original_urlopen
            push_to_feishu.time.sleep = original_sleep

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result["data"]["ok"], True)

    def test_non_idempotent_post_is_not_retried_by_default(self) -> None:
        calls = {"count": 0}
        original_urlopen = push_to_feishu.urlopen

        def flaky_urlopen(*_args, **_kwargs):
            calls["count"] += 1
            raise TimeoutError("The read operation timed out")

        try:
            push_to_feishu.urlopen = flaky_urlopen
            with self.assertRaisesRegex(RuntimeError, "status unknown and not retried"):
                push_to_feishu.request_json(
                    "POST",
                    "/bitable/v1/apps/app/tables/table/records/batch_create",
                    body={"records": [{"fields": {"标题": "A"}}]},
                )
        finally:
            push_to_feishu.urlopen = original_urlopen

        self.assertEqual(calls["count"], 1)

    def test_max_attempts_error_is_visible_for_safe_request(self) -> None:
        calls = {"count": 0}
        original_urlopen = push_to_feishu.urlopen
        original_sleep = push_to_feishu.time.sleep

        def flaky_urlopen(*_args, **_kwargs):
            calls["count"] += 1
            raise TimeoutError("The read operation timed out")

        try:
            push_to_feishu.urlopen = flaky_urlopen
            push_to_feishu.time.sleep = lambda *_args, **_kwargs: None
            with self.assertRaisesRegex(RuntimeError, "failed after 2 attempts; status unknown"):
                push_to_feishu.request_json("PUT", "/bitable/v1/apps/app/tables/table/records/rec", attempts=2)
        finally:
            push_to_feishu.urlopen = original_urlopen
            push_to_feishu.time.sleep = original_sleep

        self.assertEqual(calls["count"], 2)

    def test_notification_failure_is_persisted_as_status_unknown(self) -> None:
        original_log_dir = feishu_automation_notify.LOG_DIR
        original_tenant_token = feishu_automation_notify.feishu.tenant_token
        original_send_text = feishu_automation_notify.send_text

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                feishu_automation_notify.LOG_DIR = Path(tmpdir)
                feishu_automation_notify.feishu.tenant_token = lambda: "token"

                def failing_send_text(*_args, **_kwargs):
                    raise RuntimeError("POST /im/v1/messages failed with transient error; status unknown")

                feishu_automation_notify.send_text = failing_send_text
                with self.assertRaisesRegex(RuntimeError, "status unknown"):
                    feishu_automation_notify.notify("测试通知", "body", targets=[("chat_id", "oc_xxx")])
            finally:
                feishu_automation_notify.LOG_DIR = original_log_dir
                feishu_automation_notify.feishu.tenant_token = original_tenant_token
                feishu_automation_notify.send_text = original_send_text

            logs = list(Path(tmpdir).glob("feishu_notification_failures_*.jsonl"))
            self.assertEqual(len(logs), 1)
            event = json.loads(logs[0].read_text(encoding="utf-8").strip())
            self.assertEqual(event["delivery_status"], "unknown")
            self.assertEqual(event["retry_policy"], "not_retried_to_avoid_duplicate_notification")


if __name__ == "__main__":
    unittest.main()
