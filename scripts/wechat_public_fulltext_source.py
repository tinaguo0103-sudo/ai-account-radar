#!/usr/bin/env python3
"""Discover exact WeChat article URLs publicly, then read their full text."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from url_content_resolver import resolve_wechat  # noqa: E402

DEFAULT_CONFIG = ROOT / "config" / "wechat_public_fulltext_sources.json"
DEFAULT_STATE = ROOT / "output" / "state" / "wechat_public_fulltext_seen.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AIAccountRadar/1.0"
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(10_000_000).decode("utf-8", errors="replace")


def discover_articles(page: str, account_name: str) -> list[dict[str, str]]:
    blocks = re.findall(r'<article\s+class="article"[^>]*data-account="([^"]+)"[^>]*>(.*?)</article>', page, flags=re.S)
    rows: list[dict[str, str]] = []
    for account, block in blocks:
        if html.unescape(account).strip() != account_name:
            continue
        match = re.search(r'<a\s+class="article-title"\s+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not match:
            continue
        raw_url = html.unescape(match.group(1)).replace("http://", "https://", 1).split("#", 1)[0]
        title = re.sub(r"<[^>]+>", "", html.unescape(match.group(2))).strip()
        rows.append({"account_name": account_name, "title": title, "url": raw_url})
    return rows


def urllib_host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or ""


def item_row(item: Any, run_id: str) -> dict[str, Any]:
    return {
        "来源类型": item.source_type,
        "平台": item.platform,
        "账号名/公众号名": item.account_name,
        "内容标题": item.content_title,
        "内容链接": item.content_url,
        "内容形态": item.content_shape,
        "正文/口播": item.body_or_transcript,
        "摘要/描述": item.summary_or_description,
        "发布时间": item.published_at,
        "原始载荷路径": item.raw_payload_path,
        "抓取方式": item.fetch_method,
        "抓取状态": item.fetch_status,
        "失败原因": item.failure_reason,
        "内容指纹": item.content_fingerprint,
        "运行批次": run_id,
        "候选时态": "today_new",
    }


def run_business_date(run_id: str) -> date:
    return datetime.strptime(run_id[4:12], "%Y%m%d").date()


def published_business_date(value: str) -> date:
    normalized = str(value or "").strip().replace("Z", "+00:00")
    if not normalized:
        raise ValueError("published_at_missing")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(BUSINESS_TIMEZONE).date()


def source_outcome(source_id: str, status: str, *, ok: bool, rows: int = 0, **details: Any) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "status": status,
        "ok": ok,
        "rows": rows,
        "artifact_count": rows,
        "substitute_count": 0,
        **details,
    }


def collect_source(
    source: dict[str, Any],
    run_id: str,
    raw_dir: Path,
    seen: dict[str, Any],
    limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_id = str(source["source_id"])
    account = str(source["account_name"])
    try:
        discovered = discover_articles(fetch_text(str(source["discovery_url"])), account)
    except Exception as exc:
        return source_outcome(source_id, "discovery_failed", ok=False, reason=type(exc).__name__), []
    if not discovered:
        return source_outcome(source_id, "discovery_empty", ok=False), []

    business_date = run_business_date(run_id)
    current_items: list[Any] = []
    stale_count = 0
    for article in discovered:
        if urllib_host(article["url"]) != "mp.weixin.qq.com":
            return source_outcome(source_id, "article_url_wrong_host", ok=False), []
        try:
            resolved = resolve_wechat(article["url"], raw_dir)
        except Exception as exc:
            return source_outcome(source_id, "fulltext_failed", ok=False, reason=type(exc).__name__), []
        item = resolved[0] if resolved else None
        if not item or item.fetch_status != "success":
            return source_outcome(
                source_id,
                "fulltext_failed",
                ok=False,
                reason=getattr(item, "failure_reason", "empty"),
            ), []
        if item.account_name != account:
            return source_outcome(source_id, "article_account_mismatch", ok=False), []
        if item.content_title != article["title"]:
            return source_outcome(source_id, "article_title_mismatch", ok=False), []
        if len(item.body_or_transcript) < 500:
            return source_outcome(source_id, "article_fulltext_too_short", ok=False), []
        try:
            item_date = published_business_date(item.published_at)
        except (TypeError, ValueError):
            return source_outcome(source_id, "article_published_at_invalid", ok=False), []
        if item_date != business_date:
            stale_count += 1
            continue
        current_items.append(item)
        if len(current_items) >= max(1, limit):
            break

    if not current_items:
        return source_outcome(
            source_id,
            "no_current_day_article",
            ok=False,
            discovered=len(discovered),
            stale_articles=stale_count,
            run_business_date=business_date.isoformat(),
        ), []

    new_rows: list[dict[str, Any]] = []
    for item in current_items:
        if item.content_url in seen.get("urls", {}):
            continue
        new_rows.append(item_row(item, run_id))
        seen.setdefault("urls", {})[item.content_url] = {
            "source_id": source_id,
            "content_fingerprint": item.content_fingerprint,
            "first_run_id": run_id,
        }
    if not new_rows:
        return source_outcome(
            source_id,
            "updated_no_new_items",
            ok=True,
            discovered=len(discovered),
        ), []
    return source_outcome(
        source_id,
        "success",
        ok=True,
        rows=len(new_rows),
        discovered=len(discovered),
    ), new_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seen-ledger", default=str(DEFAULT_STATE))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"run_\d{8}_\d{6}(?:_[A-Za-z0-9_-]+)?", args.run_id):
        print(json.dumps({"ok": False, "status": "wrong_run_id", "rows": 0}))
        return 2
    config = read_json(Path(args.config), {})
    sources = [row for row in config.get("sources", []) if row.get("enabled") is not False]
    if not sources:
        print(json.dumps({"ok": False, "status": "no_active_wechat_source", "rows": 0}))
        return 2
    if args.check_only:
        print(json.dumps({"ok": True, "status": "planned", "sources": len(sources), "network_calls": 0}))
        return 0

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    outcomes: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen_path = Path(args.seen_ledger)
    seen = read_json(seen_path, {"schema_version": 1, "urls": {}})
    for source in sources:
        outcome, source_rows = collect_source(source, args.run_id, raw_dir, seen, args.limit)
        outcomes.append(outcome)
        rows.extend(source_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    manual = out_dir / "content_items_manual.jsonl"
    manual.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen["updated_at"] = datetime.now(timezone.utc).isoformat()
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = {
        "ok": all(row["ok"] for row in outcomes),
        "status": "completed" if all(row["status"] in {"success", "updated_no_new_items"} for row in outcomes) else "completed_with_failures",
        "run_id": args.run_id,
        "rows": len(rows),
        "outcomes": outcomes,
        "manual_artifact": {
            "path": str(manual.resolve()),
            "row_count": len(rows),
            "sha256": hashlib.sha256(manual.read_bytes()).hexdigest(),
        },
        "provider": "public_discovery_exact_wechat_fulltext",
        "legacy_wewe_used": False,
    }
    (out_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
