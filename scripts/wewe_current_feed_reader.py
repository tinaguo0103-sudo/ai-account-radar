#!/usr/bin/env python3
"""Read only receipt-bound current WeWe articles through bounded feed pages."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from wechat_fulltext_provider_probe import Provider, html_to_text, to_manual_row, write_outputs
from wewe_provider_health import CANONICAL_DATA_DIR, validate_refresh_receipt
from wewe_provider_refresh import HEALTH_DIR, PROVIDER_URL


MAX_ARTICLE_RESPONSE_BYTES = 8_000_000
MIN_FULLTEXT_CHARS = 800


class CurrentFeedError(ValueError):
    pass


def _exact_int(value: Any, reason: str) -> int:
    if type(value) is not int:
        raise CurrentFeedError(reason)
    return value


def load_refresh_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise CurrentFeedError("refresh_result_unreadable") from exc
    required = {"attempt_id", "feed_count", "new_item_count", "ok", "receipt_path", "receipt_sha256", "run_id", "secret_material_read", "secrets_exposed", "starts_browser", "starts_provider", "status"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise CurrentFeedError("refresh_result_schema_invalid")
    if payload["ok"] is not True or payload["status"] != "success":
        raise CurrentFeedError("refresh_result_not_success")
    return payload


def _db_identity(database: Path) -> dict[str, Any]:
    try:
        info = database.stat()
    except OSError as exc:
        raise CurrentFeedError("current_feed_database_unreadable") from exc
    return {"path": str(database.resolve()), "device": info.st_dev, "inode": info.st_ino}


def receipt_bound_plan(database: Path, receipt: dict[str, Any]) -> list[dict[str, Any]]:
    if _db_identity(database) != receipt["database_identity"]:
        raise CurrentFeedError("current_feed_database_identity_drift")
    before_by_feed = {row["feed_id"]: row for row in receipt["before"]["feeds"]}
    after_by_feed = {row["feed_id"]: row for row in receipt["after"]["feeds"]}
    planned: list[dict[str, Any]] = []
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        for feed_id in receipt["feed_ids"]:
            old = before_by_feed[feed_id]
            new = after_by_feed[feed_id]
            live = connection.execute(
                "select status, sync_time, updated_at, mp_name from feeds where id=?", (feed_id,)
            ).fetchall()
            if len(live) != 1 or live[0][:3] != (1, new["sync_time"], new["updated_at_ms"]):
                raise CurrentFeedError("current_feed_revision_drift")
            if not isinstance(live[0][3], str) or not live[0][3]:
                raise CurrentFeedError("current_feed_owner_missing")
            provider_rows = connection.execute(
                "select id, title, publish_time from articles where mp_id=? order by publish_time desc, rowid asc",
                (feed_id,),
            ).fetchall()
            if len(provider_rows) != new["article_count"]:
                raise CurrentFeedError("current_feed_article_count_drift")
            current = sorted(
                (row for row in provider_rows if old["max_publish_time"] < row[2] <= new["max_publish_time"]),
                key=lambda row: (-row[2], row[0]),
            )
            expected = new["article_count"] - old["article_count"]
            if len(current) != expected:
                raise CurrentFeedError("current_feed_watermark_count_mismatch")
            positions = {row[0]: index + 1 for index, row in enumerate(provider_rows)}
            for article_id, title, publish_time in current:
                if not all(isinstance(value, str) and value for value in (article_id, title)) or type(publish_time) is not int:
                    raise CurrentFeedError("current_feed_article_schema_invalid")
                planned.append({
                    "feed_id": feed_id,
                    "feed_name": live[0][3],
                    "article_id": article_id,
                    "title": title,
                    "publish_time": publish_time,
                    "page": positions[article_id],
                    "refresh_revision": receipt["refresh_revision"],
                })
        connection.close()
    except sqlite3.Error as exc:
        raise CurrentFeedError("current_feed_database_query_failed") from exc
    if len(planned) != receipt["new_item_count"]:
        raise CurrentFeedError("current_feed_total_count_mismatch")
    identities = [(row["feed_id"], row["article_id"]) for row in planned]
    if len(set(identities)) != len(identities):
        raise CurrentFeedError("current_feed_duplicate_identity")
    return planned


def bounded_fetch(url: str, *, max_bytes: int = MAX_ARTICLE_RESPONSE_BYTES) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "AIAccountRadar/AR034C"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(max_bytes + 1)
            content_type = response.headers.get("Content-Type", "")
    except Exception as exc:
        raise CurrentFeedError(f"current_feed_page_request_failed:{type(exc).__name__}") from exc
    if len(raw) > max_bytes:
        raise CurrentFeedError("current_feed_page_too_large")
    return raw, content_type


def _article_id(item: dict[str, Any]) -> str:
    for value in (item.get("id"), item.get("url"), item.get("external_url")):
        if isinstance(value, str) and value:
            path = urllib.parse.urlparse(value).path.rstrip("/")
            if "/s/" in path:
                return path.rsplit("/s/", 1)[1]
    return ""


def fetch_planned_fulltext(
    planned: list[dict[str, Any]], *, fetcher: Callable[[str], tuple[bytes, str]] = bounded_fetch,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for expected in planned:
        query = urllib.parse.urlencode({"limit": 1, "page": expected["page"], "mode": "fulltext"})
        url = f"{PROVIDER_URL}/feeds/{urllib.parse.quote(expected['feed_id'], safe='')}.json?{query}"
        raw, content_type = fetcher(url)
        if "json" not in content_type.lower():
            raise CurrentFeedError("current_feed_page_content_type_invalid")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CurrentFeedError("current_feed_page_malformed") from exc
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise CurrentFeedError("current_feed_page_coverage_invalid")
        item = items[0]
        if _article_id(item) != expected["article_id"] or item.get("title") != expected["title"]:
            raise CurrentFeedError("current_feed_page_identity_mismatch")
        published = item.get("date_published") or item.get("date_modified") or ""
        body_html = item.get("content_html")
        if not isinstance(published, str) or not isinstance(body_html, str):
            raise CurrentFeedError("current_feed_page_schema_invalid")
        body = html_to_text(body_html)
        if len(body) < MIN_FULLTEXT_CHARS:
            raise CurrentFeedError("current_feed_fulltext_insufficient")
        rows.append({
            "title": expected["title"],
            "url": f"https://mp.weixin.qq.com/s/{expected['article_id']}",
            "published_at": published,
            "body": body,
            "raw_body": body_html,
            "author": "",
            "raw_payload_path": "",
        })
    return rows


def run(
    *, refresh_result_path: Path, run_id: str, run_started_at_ms: int,
    data_dir: Path = CANONICAL_DATA_DIR, health_dir: Path = HEALTH_DIR,
    check_only: bool = False, out: Path | None = None, csv_path: Path | None = None,
    fetcher: Callable[[str], tuple[bytes, str]] = bounded_fetch,
) -> dict[str, Any]:
    result = load_refresh_result(refresh_result_path)
    if result["run_id"] != run_id:
        raise CurrentFeedError("current_feed_run_mismatch")
    receipt = validate_refresh_receipt(
        Path(result["receipt_path"]), result["receipt_sha256"], run_id=run_id,
        attempt_id=result["attempt_id"], data_dir=data_dir, health_dir=health_dir,
        now_ms=max(_exact_int(run_started_at_ms, "current_feed_run_start_invalid"), receipt_now_ms(result, health_dir)),
        run_started_at_ms=run_started_at_ms,
    )
    database = data_dir.resolve() / "wewe-rss.db"
    planned = receipt_bound_plan(database, receipt)
    base = {
        "ok": True, "status": "planned" if check_only else "success", "run_id": run_id,
        "attempt_id": result["attempt_id"], "receipt_sha256": result["receipt_sha256"],
        "refresh_revision": receipt["refresh_revision"], "planned_items": len(planned),
        "feed_ids": receipt["feed_ids"], "article_ids": [row["article_id"] for row in planned],
        "uses_full_feed_json": False, "provider_requests": 0 if check_only else len(planned),
        "refresh_requested": False, "writes_feishu": False, "sends_topic_card": False,
        "triggers_script_generation": False,
    }
    if check_only:
        return base
    items = fetch_planned_fulltext(planned, fetcher=fetcher)
    if len(items) != len(planned):
        raise CurrentFeedError("current_feed_fulltext_count_mismatch")
    # Revalidate the signed receipt and live DB after all bounded reads.
    validate_refresh_receipt(
        Path(result["receipt_path"]), result["receipt_sha256"], run_id=run_id,
        attempt_id=result["attempt_id"], data_dir=data_dir, health_dir=health_dir,
        now_ms=receipt_now_ms(result, health_dir), run_started_at_ms=run_started_at_ms,
    )
    if receipt_bound_plan(database, receipt) != planned:
        raise CurrentFeedError("current_feed_plan_drift")
    manual_rows = []
    for expected, item in zip(planned, items):
        provider = Provider(
            provider_id="wewe-rss", provider="wewe-rss", name="WeWe-RSS current feed",
            source_name=expected["feed_name"], platform="微信公众号", source_type="公众号文章",
            base_url=PROVIDER_URL, feed_path=f"/feeds/{expected['feed_id']}.json",
            source_id=expected["feed_id"],
        )
        manual_rows.append(to_manual_row(provider, item, "success", "", refresh_revision=receipt["refresh_revision"]))
    if out is None or csv_path is None:
        raise CurrentFeedError("current_feed_output_path_missing")
    write_outputs(manual_rows, out, csv_path)
    return {**base, "output": str(out), "csv": str(csv_path), "fulltext_items": len(manual_rows)}


def receipt_now_ms(result: dict[str, Any], health_dir: Path) -> int:
    try:
        receipt = json.loads((health_dir.resolve() / "receipts" / f"{result['run_id']}_{result['attempt_id']}.json").read_text(encoding="utf-8"))
        return _exact_int(receipt["completed_at_ms"], "refresh_receipt_time_invalid") + 1
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise CurrentFeedError("refresh_receipt_time_unreadable") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Read receipt-bound current WeWe articles without downloading the full feed.")
    parser.add_argument("--refresh-result", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-started-at-ms", required=True, type=int)
    parser.add_argument("--out")
    parser.add_argument("--csv")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = run(
            refresh_result_path=Path(args.refresh_result), run_id=args.run_id,
            run_started_at_ms=args.run_started_at_ms, check_only=args.check_only,
            out=Path(args.out) if args.out else None, csv_path=Path(args.csv) if args.csv else None,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (CurrentFeedError, ValueError, OSError) as exc:
        print(json.dumps({
            "ok": False, "status": "current_feed_read_failed", "reason": str(exc),
            "run_id": args.run_id, "refresh_requested": False, "writes_feishu": False,
            "sends_topic_card": False, "triggers_script_generation": False,
        }, ensure_ascii=False, sort_keys=True))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
