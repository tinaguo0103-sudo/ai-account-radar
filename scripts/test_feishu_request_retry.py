#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from urllib.error import HTTPError

import feishu_automation_notify
import push_to_feishu


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status


class FeishuRequestRetryTest(unittest.TestCase):
    def test_success_request_performs_no_telemetry_write(self) -> None:
        original_urlopen = push_to_feishu.urlopen
        try:
            push_to_feishu.urlopen = lambda *_args, **_kwargs: FakeResponse({"code": 0, "data": {"ok": True}})
            result = push_to_feishu.request_json(
                "PUT",
                "/bitable/v1/apps/app_token_secret/tables/table123/records/rec456",
                token="tenant-secret",
                body={"fields": {"正文": "private body"}},
            )
        finally:
            push_to_feishu.urlopen = original_urlopen
        self.assertTrue(result["data"]["ok"])
        for name in ("record_request_telemetry", "safe_write_feishu_request_telemetry", "write_feishu_request_telemetry"):
            self.assertFalse(hasattr(push_to_feishu, name))

    def test_get_retries_transient_timeout(self) -> None:
        calls = {"count": 0}
        original_urlopen = push_to_feishu.urlopen
        original_sleep = push_to_feishu.time.sleep

        def flaky_urlopen(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("The read operation timed out")
            return FakeResponse({"code": 0, "data": {"ok": True}})

        stderr = io.StringIO()
        try:
            push_to_feishu.urlopen = flaky_urlopen
            push_to_feishu.time.sleep = lambda *_args, **_kwargs: None
            with redirect_stderr(stderr):
                result = push_to_feishu.request_json(
                    "GET",
                    "/bitable/v1/apps/app_token_secret/tables?page_token=query_secret",
                )
        finally:
            push_to_feishu.urlopen = original_urlopen
            push_to_feishu.time.sleep = original_sleep
        self.assertEqual(2, calls["count"])
        self.assertTrue(result["data"]["ok"])
        self.assertNotIn("app_token_secret", stderr.getvalue())
        self.assertNotIn("query_secret", stderr.getvalue())

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
                    "/bitable/v1/apps/app_token_secret/tables/table/records/batch_create",
                    body={"records": [{"fields": {"标题": "A"}}]},
                )
        finally:
            push_to_feishu.urlopen = original_urlopen
        self.assertEqual(1, calls["count"])

    def test_safe_request_max_attempts_remain_visible(self) -> None:
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
        self.assertEqual(2, calls["count"])

    def test_http_error_keeps_unknown_status(self) -> None:
        original_urlopen = push_to_feishu.urlopen

        def failing_urlopen(*_args, **_kwargs):
            raise HTTPError(
                "https://open.feishu.cn/open-apis/bitable/v1/apps/app/tables/table/records/rec",
                503, "Service Unavailable", {}, io.BytesIO(b'{"code":999}'),
            )

        try:
            push_to_feishu.urlopen = failing_urlopen
            with self.assertRaisesRegex(RuntimeError, "failed after 1 attempts; status unknown"):
                push_to_feishu.request_json("GET", "/bitable/v1/apps/app/tables/table/records/rec", attempts=1)
        finally:
            push_to_feishu.urlopen = original_urlopen

    def test_api_error_is_visible_without_observation_followup(self) -> None:
        original_urlopen = push_to_feishu.urlopen
        try:
            push_to_feishu.urlopen = lambda *_args, **_kwargs: FakeResponse({"code": 91403, "msg": "Forbidden"})
            with self.assertRaisesRegex(RuntimeError, "failed"):
                push_to_feishu.request_json("POST", "/bitable/v1/apps/app/tables/table/records")
        finally:
            push_to_feishu.urlopen = original_urlopen

    def test_notification_failure_is_persisted_as_status_unknown(self) -> None:
        original_log_dir = feishu_automation_notify.LOG_DIR
        original_tenant_token = feishu_automation_notify.feishu.tenant_token
        original_send_text = feishu_automation_notify.send_text
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                feishu_automation_notify.LOG_DIR = Path(tmpdir)
                feishu_automation_notify.feishu.tenant_token = lambda: "token"
                feishu_automation_notify.send_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("POST /im/v1/messages failed with transient error; status unknown")
                )
                with self.assertRaisesRegex(RuntimeError, "status unknown"):
                    feishu_automation_notify.notify("测试通知", "body", targets=[("chat_id", "oc_xxx")])
            finally:
                feishu_automation_notify.LOG_DIR = original_log_dir
                feishu_automation_notify.feishu.tenant_token = original_tenant_token
                feishu_automation_notify.send_text = original_send_text
            logs = list(Path(tmpdir).glob("feishu_notification_failures_*.jsonl"))
            self.assertEqual(1, len(logs))


if __name__ == "__main__":
    unittest.main()
