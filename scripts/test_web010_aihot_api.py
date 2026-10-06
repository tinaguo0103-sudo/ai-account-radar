#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock

import content_sampler
from source_control import SourceControl


SELECTED_URL = "https://aihot.news/api/v1/items?mode=selected&window=7d&limit=30&by=published"
DAILY_URL = "https://aihot.news/api/v1/dailies/latest"


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers: dict[str, str] | None = None):
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def http_error(code: int, headers: dict[str, str] | None = None) -> HTTPError:
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return HTTPError("https://aihot.news/api/v1/items", code, "test", message, None)


def selected_source(url: str = SELECTED_URL) -> dict[str, str]:
    return {"id": "aihot_selected", "account_name": "AIHOT精选", "url": url}


def daily_source(url: str = DAILY_URL) -> dict[str, str]:
    return {"id": "aihot_daily", "account_name": "AIHOT日报", "url": url}


class AIHOTApiIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "source-control.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def json_response(data: dict) -> FakeResponse:
        return FakeResponse(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def test_selected_200_then_same_query_304_reuses_exact_validated_body(self):
        payload = {
            "schemaVersion": 1,
            "query": {"by": "published", "window": "7d", "limit": 30},
            "items": [{
                "id": "stable-1", "title": "模型发布",
                "originalTitle": "Model release", "summary": "摘要",
                "source": {"name": "X：作者 (@author)"},
                "links": {"original": "https://example.com/original/1"},
                "publishedAt": "2026-10-06T04:00:00Z",
            }],
            "page": {"nextCursor": None},
        }
        responses = [self.json_response(payload)]
        seen_requests = []

        def opener(request, **_kwargs):
            seen_requests.append(request)
            if responses:
                response = responses.pop(0)
                response.headers["ETag"] = '"selected-v1"'
                return response
            raise http_error(304)

        with mock.patch.object(content_sampler, "urlopen", side_effect=opener):
            first, first_status, first_detail = content_sampler.fetch_aihot_payload(
                selected_source(), self.db_path
            )
            second, second_status, second_detail = content_sampler.fetch_aihot_payload(
                selected_source(), self.db_path
            )

        self.assertEqual((first_status, first_detail), ("ok", ""))
        self.assertEqual((second_status, second_detail), ("not_modified_cache_reused", ""))
        self.assertEqual([row.fingerprint for row in first], [row.fingerprint for row in second])
        self.assertEqual(first[0].url, "https://example.com/original/1")
        self.assertEqual(first[0].account_name, "X：作者 (@author)")
        self.assertEqual(first[0].published_at, "2026-10-06T04:00:00Z")
        self.assertEqual(seen_requests[0].get_header("If-none-match"), None)
        self.assertEqual(seen_requests[1].get_header("If-none-match"), '"selected-v1"')

    def test_query_change_does_not_reuse_prior_etag_or_body(self):
        with mock.patch.object(content_sampler, "urlopen", side_effect=http_error(304)) as opener:
            rows, status, detail = content_sampler.fetch_aihot_payload(
                selected_source(SELECTED_URL.replace("limit=30", "limit=29")), self.db_path
            )
        self.assertEqual(rows, [])
        self.assertEqual(status, "failed:aihot_304_cache_invalid")
        self.assertEqual(detail, "aihot_304_without_exact_cache")
        request = opener.call_args.args[0]
        self.assertIsNone(request.get_header("If-none-match"))

    def test_bad_exact_cache_on_304_fails_closed(self):
        endpoint, query_json, cache_key, parser_version = content_sampler.canonical_aihot_request(SELECTED_URL)
        cache = SourceControl(self.db_path)
        bad_body = '{"items":"not-a-list"}'
        cache.put_http_cache(
            cache_key=cache_key,
            endpoint=endpoint,
            query_json=query_json,
            parser_version=parser_version,
            etag='"bad"',
            body_sha256=hashlib.sha256(bad_body.encode()).hexdigest(),
            body=bad_body,
        )
        with mock.patch.object(content_sampler, "urlopen", side_effect=http_error(304)):
            rows, status, detail = content_sampler.fetch_aihot_payload(selected_source(), self.db_path)
        self.assertEqual(rows, [])
        self.assertEqual(status, "failed:aihot_304_cache_invalid")
        self.assertIn("aihot_cache_invalid:selected_items_missing", detail)

    def test_parse_failure_is_not_cached(self):
        with mock.patch.object(content_sampler, "urlopen", return_value=self.json_response({"items": "bad"})):
            rows, status, _detail = content_sampler.fetch_aihot_payload(selected_source(), self.db_path)
        self.assertEqual(rows, [])
        self.assertEqual(status, "failed:selected_items_missing")
        endpoint, query_json, cache_key, _version = content_sampler.canonical_aihot_request(SELECTED_URL)
        self.assertIsNone(SourceControl(self.db_path).get_http_cache(cache_key))

    def test_new_daily_report_shape_and_true_empty_report(self):
        source = daily_source()
        empty_payload = {"schemaVersion": 1, "report": {
            "date": "2026-10-06", "generatedAt": "2026-10-06T05:00:00Z", "sections": [],
        }}
        with mock.patch.object(content_sampler, "urlopen", return_value=self.json_response(empty_payload)):
            rows, status, detail = content_sampler.fetch_aihot_payload(source, self.db_path)
        self.assertEqual((rows, status, detail), ([], "ok", ""))

        daily_payload = {"schemaVersion": 1, "report": {
            "date": "2026-10-06", "generatedAt": "2026-10-06T05:00:00Z", "sections": [{
                "label": "模型", "items": [{
                    "id": "daily-1", "title": "日报条目", "summary": "摘要",
                    "source": {"name": "X：日报作者"},
                    "links": {"original": "https://example.com/daily/1"},
                    "publishedAt": "2026-10-06T04:30:00Z",
                }],
            }],
        }}
        with mock.patch.object(content_sampler, "urlopen", return_value=self.json_response(daily_payload)):
            rows, status, detail = content_sampler.fetch_aihot_payload(source, self.db_path)
        self.assertEqual((status, detail), ("ok", ""))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].url, "https://example.com/daily/1")
        self.assertEqual(rows[0].account_name, "X：日报作者")
        self.assertEqual(rows[0].published_at, "2026-10-06T04:30:00Z")

    def test_legacy_selected_and_daily_shapes_preserve_order_and_identity(self):
        source = selected_source()
        old = {"items": [
            {"id": "1", "title": "A", "summary": "one", "source": "Publisher", "url": "https://example.com/a"},
            {"id": "2", "title": "B", "summary": "two", "source": "Publisher", "url": "https://example.com/b"},
        ]}
        new = {"schemaVersion": 1, "items": [
            {"id": "1", "title": "A", "summary": "one", "source": {"name": "Publisher"}, "links": {"original": "https://example.com/a"}},
            {"id": "2", "title": "B", "summary": "two", "source": {"name": "Publisher"}, "links": {"original": "https://example.com/b"}},
        ]}
        old_rows, _ = content_sampler.parse_aihot_payload(old, source)
        new_rows, _ = content_sampler.parse_aihot_payload(new, source)
        self.assertEqual([row.fingerprint for row in old_rows], [row.fingerprint for row in new_rows])
        self.assertEqual([row.title for row in new_rows], ["A", "B"])

        daily = daily_source()
        old_daily = {"date": "2026-10-06", "sections": [{"label": "模型", "items": [
            {"title": "A", "summary": "one", "sourceName": "Publisher", "sourceUrl": "https://example.com/a"},
        ]}]}
        new_daily = {"report": {"date": "2026-10-06", "sections": [{"label": "模型", "items": [
            {"title": "A", "summary": "one", "source": {"name": "Publisher"}, "links": {"original": "https://example.com/a"}},
        ]}]}}
        old_rows, _ = content_sampler.parse_aihot_payload(old_daily, daily)
        new_rows, _ = content_sampler.parse_aihot_payload(new_daily, daily)
        self.assertEqual([row.fingerprint for row in old_rows], [row.fingerprint for row in new_rows])

    def test_retry_after_seconds_and_http_date_are_obeyed_for_429_and_503(self):
        fixed_now = datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)
        for code, retry_after in ((429, "2"), (503, "Tue, 06 Oct 2026 05:00:03 GMT")):
            with self.subTest(code=code, retry_after=retry_after):
                calls = 0
                sleeps = []

                def opener(_request, **_kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise http_error(code, {"Retry-After": retry_after})
                    return self.json_response({"items": []})

                with mock.patch.object(content_sampler, "urlopen", side_effect=opener):
                    rows, status, detail = content_sampler.fetch_aihot_payload(
                        selected_source(),
                        Path(self.temp.name) / f"cache-{code}.sqlite3",
                        sleeper=sleeps.append,
                        wall_clock=lambda: fixed_now,
                    )
                self.assertEqual((rows, status, detail), ([], "ok", ""))
                self.assertEqual(calls, 2)
                self.assertEqual(sleeps, [2.0 if code == 429 else 3.0])

    def test_retry_budget_exhaustion_does_not_retry_early(self):
        with mock.patch.object(content_sampler, "urlopen", side_effect=http_error(503, {"Retry-After": "31"})) as opener:
            rows, status, _detail = content_sampler.fetch_aihot_payload(selected_source(), self.db_path)
        self.assertEqual(rows, [])
        self.assertEqual(status, "failed:aihot_rate_limited_retry_budget_exhausted_http_503")
        self.assertEqual(opener.call_count, 1)

    def test_missing_retry_after_is_visible_and_not_retried(self):
        with mock.patch.object(content_sampler, "urlopen", side_effect=http_error(429)) as opener:
            _rows, status, _detail = content_sampler.fetch_aihot_payload(selected_source(), self.db_path)
        self.assertEqual(status, "failed:aihot_rate_limited_retry_after_missing_http_429")
        self.assertEqual(opener.call_count, 1)


if __name__ == "__main__":
    unittest.main()
