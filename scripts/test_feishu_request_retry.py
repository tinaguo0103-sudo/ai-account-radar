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
    def setUp(self) -> None:
        self.telemetry_tmpdir = tempfile.TemporaryDirectory()
        self.original_log_dir = push_to_feishu.LOG_DIR
        push_to_feishu.LOG_DIR = Path(self.telemetry_tmpdir.name)

    def tearDown(self) -> None:
        push_to_feishu.LOG_DIR = self.original_log_dir
        self.telemetry_tmpdir.cleanup()

    def telemetry_events(self) -> list[dict]:
        logs = list(Path(self.telemetry_tmpdir.name).glob("feishu_request_telemetry_*.jsonl"))
        events: list[dict] = []
        for path in logs:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        return events

    def test_success_request_records_sanitized_telemetry(self) -> None:
        original_urlopen = push_to_feishu.urlopen

        def ok_urlopen(*_args, **_kwargs):
            return FakeResponse({"code": 0, "data": {"ok": True}}, status=200)

        try:
            push_to_feishu.urlopen = ok_urlopen
            result = push_to_feishu.request_json(
                "PUT",
                "/bitable/v1/apps/app_token_secret/tables/table123/records/rec456?receive_id_type=open_id&page_token=query_secret",
                token="tenant-secret",
                body={"fields": {"正文": "private body"}},
            )
        finally:
            push_to_feishu.urlopen = original_urlopen

        self.assertEqual(result["data"]["ok"], True)
        events = self.telemetry_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["method"], "PUT")
        self.assertEqual(event["path_template"], "/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}?page_token=<redacted>&receive_id_type=<redacted>")
        self.assertEqual(event["table_id"], "table123")
        self.assertEqual(event["record_id"], "rec456")
        self.assertGreater(event["payload_size_bytes"], 0)
        self.assertEqual(event["status_code"], 200)
        self.assertEqual(event["error_kind"], "none")
        self.assertFalse(event["status_unknown"])
        telemetry_text = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("tenant-secret", telemetry_text)
        self.assertNotIn("app_token_secret", telemetry_text)
        self.assertNotIn("query_secret", telemetry_text)
        self.assertNotIn("private body", telemetry_text)
        self.assertNotIn("Authorization", telemetry_text)

    def test_action_endpoint_is_not_treated_as_record_id(self) -> None:
        original_urlopen = push_to_feishu.urlopen

        def ok_urlopen(*_args, **_kwargs):
            return FakeResponse({"code": 0, "data": {"ok": True}}, status=200)

        try:
            push_to_feishu.urlopen = ok_urlopen
            push_to_feishu.request_json(
                "POST",
                "/bitable/v1/apps/app_token_secret/tables/table123/records/batch_create",
                body={"records": [{"fields": {"标题": "A"}}]},
                retry=True,
            )
        finally:
            push_to_feishu.urlopen = original_urlopen

        events = self.telemetry_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["path_template"], "/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create")
        self.assertEqual(events[0]["table_id"], "table123")
        self.assertIsNone(events[0]["record_id"])

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
                    "/bitable/v1/apps/app_token_secret/tables?receive_id_type=open_id&page_token=query_secret",
                )
        finally:
            push_to_feishu.urlopen = original_urlopen
            push_to_feishu.time.sleep = original_sleep

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result["data"]["ok"], True)
        events = self.telemetry_events()
        self.assertEqual([event["attempt"] for event in events], [1, 2])
        self.assertEqual(events[0]["error_kind"], "timeout")
        self.assertTrue(events[0]["will_retry"])
        self.assertEqual(events[0]["retry_decision"], "retry")
        self.assertTrue(events[0]["status_unknown"])
        self.assertEqual(events[1]["error_kind"], "none")
        self.assertFalse(events[1]["will_retry"])
        warning_text = stderr.getvalue()
        self.assertIn("/bitable/v1/apps/{app_token}/tables?page_token=<redacted>&receive_id_type=<redacted>", warning_text)
        self.assertNotIn("app_token_secret", warning_text)
        self.assertNotIn("query_secret", warning_text)

    def test_non_idempotent_post_is_not_retried_by_default(self) -> None:
        calls = {"count": 0}
        original_urlopen = push_to_feishu.urlopen

        def flaky_urlopen(*_args, **_kwargs):
            calls["count"] += 1
            raise TimeoutError("The read operation timed out")

        try:
            push_to_feishu.urlopen = flaky_urlopen
            with self.assertRaisesRegex(RuntimeError, "status unknown and not retried") as context:
                push_to_feishu.request_json(
                    "POST",
                    "/bitable/v1/apps/app_token_secret/tables/table/records/batch_create?page_token=query_secret",
                    body={"records": [{"fields": {"标题": "A"}}]},
                )
        finally:
            push_to_feishu.urlopen = original_urlopen

        self.assertEqual(calls["count"], 1)
        message = str(context.exception)
        self.assertIn("/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create?page_token=<redacted>", message)
        self.assertNotIn("app_token_secret", message)
        self.assertNotIn("query_secret", message)
        events = self.telemetry_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["method"], "POST")
        self.assertEqual(events[0]["path_template"], "/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create?page_token=<redacted>")
        self.assertIsNone(events[0]["record_id"])
        self.assertEqual(events[0]["retry_decision"], "retry_disabled_status_unknown")
        self.assertFalse(events[0]["will_retry"])
        self.assertTrue(events[0]["status_unknown"])

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

    def test_http_error_records_status_code_and_kind(self) -> None:
        original_urlopen = push_to_feishu.urlopen

        def failing_urlopen(*_args, **_kwargs):
            raise HTTPError(
                "https://open.feishu.cn/open-apis/bitable/v1/apps/app/tables/table/records/rec",
                503,
                "Service Unavailable",
                {},
                io.BytesIO(b'{"code": 999, "msg": "busy"}'),
            )

        try:
            push_to_feishu.urlopen = failing_urlopen
            with self.assertRaisesRegex(RuntimeError, "failed after 1 attempts; status unknown"):
                push_to_feishu.request_json(
                    "GET",
                    "/bitable/v1/apps/app/tables/table/records/rec",
                    attempts=1,
                )
        finally:
            push_to_feishu.urlopen = original_urlopen

        events = self.telemetry_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status_code"], 503)
        self.assertEqual(events[0]["error_kind"], "http_error")
        self.assertEqual(events[0]["retry_decision"], "max_attempts_reached")
        self.assertTrue(events[0]["status_unknown"])

    def test_api_error_records_telemetry_without_payload_body(self) -> None:
        original_urlopen = push_to_feishu.urlopen

        def api_error_urlopen(*_args, **_kwargs):
            return FakeResponse({"code": 91403, "msg": "Forbidden", "sensitive": "server-payload"}, status=200)

        try:
            push_to_feishu.urlopen = api_error_urlopen
            with self.assertRaisesRegex(RuntimeError, "failed"):
                push_to_feishu.request_json(
                    "POST",
                    "/bitable/v1/apps/app_token_secret/tables/table/records",
                    body={"fields": {"正文": "private body"}},
                )
        finally:
            push_to_feishu.urlopen = original_urlopen

        events = self.telemetry_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["error_kind"], "api_error")
        self.assertEqual(events[0]["feishu_code"], 91403)
        telemetry_text = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("server-payload", telemetry_text)
        self.assertNotIn("private body", telemetry_text)
        self.assertNotIn("app_token_secret", telemetry_text)

    def test_telemetry_write_failure_preserves_original_error(self) -> None:
        original_urlopen = push_to_feishu.urlopen
        original_write = push_to_feishu.write_feishu_request_telemetry

        def flaky_urlopen(*_args, **_kwargs):
            raise TimeoutError("The read operation timed out")

        def failing_telemetry_write(_event):
            raise OSError("disk full")

        try:
            push_to_feishu.urlopen = flaky_urlopen
            push_to_feishu.write_feishu_request_telemetry = failing_telemetry_write
            with self.assertRaisesRegex(RuntimeError, "failed after 1 attempts; status unknown"):
                push_to_feishu.request_json(
                    "PUT",
                    "/bitable/v1/apps/app/tables/table/records/rec",
                    attempts=1,
                )
        finally:
            push_to_feishu.urlopen = original_urlopen
            push_to_feishu.write_feishu_request_telemetry = original_write

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
