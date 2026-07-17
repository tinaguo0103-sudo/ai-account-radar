#!/usr/bin/env python3
"""Read only receipt-bound current WeWe articles through bounded feed pages."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from wechat_fulltext_provider_probe import Provider, html_to_text, to_manual_row
from wewe_provider_health import CANONICAL_DATA_DIR, validate_refresh_receipt
from wewe_provider_refresh import HEALTH_DIR, PROVIDER_URL


MAX_ARTICLE_RESPONSE_BYTES = 8_000_000
SHORT_TEXT_BOUNDARY = 800
PROVIDER_ERROR_TEXTS = {
    "获取全文失败，请重试~",
    "获取全文失败，请重试",
    "请先登录",
    "登录后查看",
}


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


def _safe_failure(expected: dict[str, Any], reason: str, **metrics: Any) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": reason,
        "feed_id": expected["feed_id"],
        "page": expected["page"],
        "article_id": expected["article_id"],
        "title": " ".join(str(expected["title"]).split())[:200],
        "artifact_count": 0,
        "response_bytes": int(metrics.get("response_bytes") or 0),
        "html_chars": int(metrics.get("html_chars") or 0),
        "text_chars": int(metrics.get("text_chars") or 0),
    }


def _known_provider_error(body_html: str, text: str) -> bool:
    compact = " ".join(text.split()).strip()
    if compact in PROVIDER_ERROR_TEXTS:
        return True
    lowered = body_html.lower()
    login_surface = any(marker in lowered for marker in ("login", "qrcode", "qr_code"))
    return len(compact) <= 200 and login_surface and any(marker in compact for marker in ("登录", "扫码", "验证"))


def fetch_planned_fulltext(
    planned: list[dict[str, Any]], *, fetcher: Callable[[str], tuple[bytes, str]] = bounded_fetch,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    successes: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for expected in planned:
        query = urllib.parse.urlencode({"limit": 1, "page": expected["page"], "mode": "fulltext"})
        url = f"{PROVIDER_URL}/feeds/{urllib.parse.quote(expected['feed_id'], safe='')}.json?{query}"
        try:
            raw, content_type = fetcher(url)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, CurrentFeedError) else f"current_feed_page_request_failed:{type(exc).__name__}"
            outcomes.append(_safe_failure(expected, reason))
            continue
        response_bytes = len(raw)
        if "json" not in content_type.lower():
            outcomes.append(_safe_failure(expected, "current_feed_page_content_type_invalid", response_bytes=response_bytes))
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            outcomes.append(_safe_failure(expected, "current_feed_page_malformed", response_bytes=response_bytes))
            continue
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            outcomes.append(_safe_failure(expected, "current_feed_page_coverage_invalid", response_bytes=response_bytes))
            continue
        item = items[0]
        if _article_id(item) != expected["article_id"] or item.get("title") != expected["title"]:
            outcomes.append(_safe_failure(expected, "current_feed_page_identity_mismatch", response_bytes=response_bytes))
            continue
        published_value = item.get("date_published") or item.get("date_modified")
        if published_value is not None and not isinstance(published_value, str):
            outcomes.append(_safe_failure(expected, "current_feed_page_schema_invalid", response_bytes=response_bytes))
            continue
        if "content_html" not in item or not isinstance(item.get("content_html"), str):
            outcomes.append(_safe_failure(expected, "current_feed_content_html_missing", response_bytes=response_bytes))
            continue
        body_html = item["content_html"]
        body = html_to_text(body_html)
        html_chars = len(body_html)
        text_chars = len(body)
        has_media = any(marker in body_html.lower() for marker in ("<img", "<video", "<audio", "<iframe"))
        if not body_html.strip() or (not body and not has_media):
            outcomes.append(_safe_failure(expected, "current_feed_content_html_empty", response_bytes=response_bytes, html_chars=html_chars, text_chars=text_chars))
            continue
        if _known_provider_error(body_html, body):
            outcomes.append(_safe_failure(expected, "current_feed_provider_error_payload", response_bytes=response_bytes, html_chars=html_chars, text_chars=text_chars))
            continue
        quality = "short_text" if text_chars < SHORT_TEXT_BOUNDARY else "normal"
        success = {
            "title": expected["title"],
            "url": f"https://mp.weixin.qq.com/s/{expected['article_id']}",
            "published_at": published_value or str(expected["publish_time"]),
            "body": body,
            "raw_body": body_html,
            "author": "",
            "raw_payload_path": "",
            "expected": expected,
            "response_bytes": response_bytes,
            "html_chars": html_chars,
            "text_chars": text_chars,
            "content_quality": quality,
        }
        successes.append(success)
        outcomes.append({
            "status": "success", "reason": "", "feed_id": expected["feed_id"],
            "page": expected["page"], "article_id": expected["article_id"], "title": expected["title"],
            "artifact_count": 1, "response_bytes": response_bytes, "html_chars": html_chars,
            "text_chars": text_chars, "content_quality": quality,
        })
    return successes, outcomes


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_atomic_batch(
    *, successes: list[dict[str, Any]], outcomes: list[dict[str, Any]], out: Path,
    csv_path: Path, report_path: Path, run_id: str, attempt_id: str,
    receipt_sha256: str, refresh_revision: int, planned_count: int,
) -> dict[str, Any]:
    if report_path.exists():
        raise CurrentFeedError("current_feed_report_already_exists")
    raw_dir = out.parent / "wewe_current_feed_raw" / f"{run_id}_{attempt_id}"
    if raw_dir.exists():
        raise CurrentFeedError("current_feed_raw_artifact_dir_exists")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".wewe-current-", dir=str(out.parent)))
    manual_rows: list[dict[str, str]] = []
    try:
        stage_raw = stage_dir / "raw"; stage_raw.mkdir()
        for success in successes:
            expected = success["expected"]
            filename = hashlib.sha256(f"{expected['feed_id']}|{expected['article_id']}".encode()).hexdigest() + ".html"
            raw_bytes = success["raw_body"].encode("utf-8")
            (stage_raw / filename).write_bytes(raw_bytes)
            final_raw_path = raw_dir / filename
            provider = Provider(
                provider_id="wewe-rss", provider="wewe-rss", name="WeWe-RSS current feed",
                source_name=expected["feed_name"], platform="微信公众号", source_type="公众号文章",
                base_url=PROVIDER_URL, feed_path=f"/feeds/{expected['feed_id']}.json", source_id=expected["feed_id"],
            )
            item = dict(success); item["raw_body"] = ""; item["raw_payload_path"] = str(final_raw_path)
            row = to_manual_row(provider, item, "success", "", refresh_revision=refresh_revision)
            row.update({
                "wewe_article_id": expected["article_id"], "wewe_feed_id": expected["feed_id"],
                "wewe_page": str(expected["page"]), "wewe_response_bytes": str(success["response_bytes"]),
                "wewe_html_chars": str(success["html_chars"]), "wewe_text_chars": str(success["text_chars"]),
                "wewe_content_quality": success["content_quality"],
                "wewe_raw_html_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            })
            row["是否全文解析"] = "是"
            row["失败原因"] = ""
            row["解析说明"] = (
                "receipt-bound bounded fulltext response verified; "
                f"content_quality={success['content_quality']}"
            )
            manual_rows.append(row)
        jsonl = ("\n".join(json.dumps(row, ensure_ascii=False) for row in manual_rows) + ("\n" if manual_rows else "")).encode("utf-8")
        csv_temp = stage_dir / "items.csv"
        with csv_temp.open("w", encoding="utf-8-sig", newline="") as handle:
            fields = list(manual_rows[0]) if manual_rows else ["内容标题", "内容链接", "抓取状态", "失败原因"]
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(manual_rows)
        csv_bytes = csv_temp.read_bytes()
        raw_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage_raw, raw_dir)
        _atomic_bytes(out, jsonl); _atomic_bytes(csv_path, csv_bytes)
        failed = [row for row in outcomes if row["status"] == "failed"]
        report = {
            "schema_version": 1, "run_id": run_id, "attempt_id": attempt_id,
            "receipt_sha256": receipt_sha256, "refresh_revision": refresh_revision,
            "status": "completed" if not failed else "completed_with_failures",
            "full_collection_success": not failed,
            "downstream_usable": bool(manual_rows) and len(outcomes) == planned_count and len(manual_rows) + len(failed) == planned_count and all(row["artifact_count"] == 0 for row in failed),
            "planned": planned_count, "attempted": len(outcomes), "succeeded": len(manual_rows), "failed": len(failed),
            "outcomes": outcomes,
            "outputs": {
                "jsonl_path": str(out), "jsonl_sha256": hashlib.sha256(jsonl).hexdigest(),
                "csv_path": str(csv_path), "csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
                "raw_dir": str(raw_dir), "raw_artifact_count": len(manual_rows),
            },
            "refresh_requested": False, "uses_full_feed_json": False, "writes_feishu": False,
            "sends_topic_card": False, "triggers_script_generation": False,
        }
        _atomic_bytes(report_path, (json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        return report
    finally:
        if stage_dir.exists():
            for child in sorted(stage_dir.rglob("*"), reverse=True):
                if child.is_file(): child.unlink()
                elif child.is_dir(): child.rmdir()
            stage_dir.rmdir()


def run(
    *, refresh_result_path: Path, run_id: str, run_started_at_ms: int,
    data_dir: Path = CANONICAL_DATA_DIR, health_dir: Path = HEALTH_DIR,
    check_only: bool = False, out: Path | None = None, csv_path: Path | None = None,
    report_path: Path | None = None,
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
    successes, outcomes = fetch_planned_fulltext(planned, fetcher=fetcher)
    # Revalidate the signed receipt and live DB after all bounded reads.
    validate_refresh_receipt(
        Path(result["receipt_path"]), result["receipt_sha256"], run_id=run_id,
        attempt_id=result["attempt_id"], data_dir=data_dir, health_dir=health_dir,
        now_ms=receipt_now_ms(result, health_dir), run_started_at_ms=run_started_at_ms,
    )
    if receipt_bound_plan(database, receipt) != planned:
        raise CurrentFeedError("current_feed_plan_drift")
    if out is None or csv_path is None or report_path is None:
        raise CurrentFeedError("current_feed_output_path_missing")
    report = write_atomic_batch(
        successes=successes, outcomes=outcomes, out=out, csv_path=csv_path, report_path=report_path,
        run_id=run_id, attempt_id=result["attempt_id"], receipt_sha256=result["receipt_sha256"],
        refresh_revision=receipt["refresh_revision"], planned_count=len(planned),
    )
    return {**base, "ok": report["full_collection_success"], "status": report["status"],
            "full_collection_success": report["full_collection_success"],
            "downstream_usable": report["downstream_usable"], "attempted": report["attempted"],
            "planned": report["planned"], "succeeded": report["succeeded"], "failed": report["failed"],
            "output": str(out), "csv": str(csv_path), "report": str(report_path),
            "fulltext_items": report["succeeded"]}


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
    parser.add_argument("--report")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = run(
            refresh_result_path=Path(args.refresh_result), run_id=args.run_id,
            run_started_at_ms=args.run_started_at_ms, check_only=args.check_only,
            out=Path(args.out) if args.out else None, csv_path=Path(args.csv) if args.csv else None,
            report_path=Path(args.report) if args.report else None,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("downstream_usable", True) else 4
    except (CurrentFeedError, ValueError, OSError) as exc:
        print(json.dumps({
            "ok": False, "status": "current_feed_read_failed", "reason": str(exc),
            "run_id": args.run_id, "refresh_requested": False, "writes_feishu": False,
            "sends_topic_card": False, "triggers_script_generation": False,
        }, ensure_ascii=False, sort_keys=True))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
