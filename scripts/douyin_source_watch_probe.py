#!/usr/bin/env python3
"""Low-frequency Douyin homepage source-watch probe.

This is a P1 probe only:
- no Feishu writes;
- no default daily_pipeline integration;
- no cookie/token/profile persistence;
- no video/audio download;
- no comments or full-history crawl.

It reads Douyin homepage sources from config/content_sources.yaml, tries a
public homepage fetch, extracts visible video ids/links when available, and
passes discovered single-video URLs to the existing url_content_resolver.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "spikes" / "douyin_source_watch_probe"
CONTENT_SOURCES = ROOT / "config" / "content_sources.yaml"
RAW_DIR = OUT / "raw"

sys.path.insert(0, str(ROOT / "scripts"))
from url_content_resolver import ContentItem, resolve_url  # noqa: E402


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def load_sources(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("sources", "content_sources"):
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return data
    raise ValueError(f"Unsupported source config shape: {path}")


def source_role(source: dict[str, Any]) -> str:
    return str(source.get("source_role") or source.get("来源角色") or source.get("role") or "")


def source_platform(source: dict[str, Any]) -> str:
    return str(source.get("platform") or source.get("平台") or "")


def source_url(source: dict[str, Any]) -> str:
    return str(source.get("url") or source.get("homepage_url") or source.get("主页链接") or "")


def source_name(source: dict[str, Any]) -> str:
    return str(source.get("account_name") or source.get("name") or source.get("名称") or "")


def selected_douyin_sources(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    roles = {"current_main_competitor", "current_aux_competitor"}
    rows = []
    for source in load_sources(path):
        if source.get("default_enabled") is False or source.get("participates_main_sampling") is False:
            continue
        if source_platform(source) != "抖音":
            continue
        if source_role(source) not in roles:
            continue
        if not source_url(source):
            rows.append(source)
            continue
        rows.append(source)
    return rows[:limit] if limit else rows


def fetch_homepage(url: str, raw_path: Path, timeout: int = 20) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(body, encoding="utf-8")
            return body, f"http_{getattr(response, 'status', 200)}"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:4000]
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(body, encoding="utf-8")
        return body, f"http_{exc.code}"
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def extract_video_ids(html: str) -> list[str]:
    ids: list[str] = []
    patterns = [
        r"/video/(\d{10,})",
        r"modal_id=(\d{10,})",
        r'"aweme_id"\s*:\s*"(\d{10,})"',
        r'"awemeId"\s*:\s*"(\d{10,})"',
        r'"group_id"\s*:\s*"(\d{10,})"',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, html):
            if match not in ids:
                ids.append(match)
    return ids


def diagnose_homepage(html: str, status: str) -> tuple[str, str]:
    if status.startswith("http_4") or status.startswith("http_5"):
        return "failed", f"主页请求失败：{status}"
    if not html:
        return "failed", "主页无返回内容"
    lowered = html.lower()
    if "captcha" in lowered or "验证码" in html or "verify" in lowered:
        return "needs_manual_verification", "疑似验证码/风控页面"
    if "登录" in html and len(html) < 20000:
        return "needs_login", "页面疑似要求登录后才能看到作品列表"
    return "partial", "公开页面可访问，但可能是 JS 壳；需要看是否能解析作品 ID"


def resolve_discovered_videos(video_ids: list[str], limit: int) -> list[ContentItem]:
    items: list[ContentItem] = []
    for video_id in video_ids[:limit]:
        url = f"https://www.douyin.com/video/{video_id}"
        try:
            items.extend(resolve_url(url, RAW_DIR))
        except Exception as exc:
            items.append(ContentItem(
                source_type="对标视频",
                platform="抖音",
                account_name="",
                content_title=url,
                content_url=url,
                content_shape="short_video",
                cover_text="",
                body_or_transcript="",
                summary_or_description="",
                published_at="",
                comments_or_questions="",
                raw_payload_path="",
                fetch_method="douyin_source_watch_probe",
                fetch_status="failed",
                failure_reason=f"{type(exc).__name__}: {exc}",
                content_fingerprint="",
            ))
        time.sleep(0.3)
    return items


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 dry-run Douyin homepage source-watch probe.")
    parser.add_argument("--config", default=str(CONTENT_SOURCES))
    parser.add_argument("--account-limit", type=int, default=3)
    parser.add_argument("--video-limit", type=int, default=3)
    parser.add_argument("--out-dir", default=str(OUT))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    sources = selected_douyin_sources(Path(args.config), args.account_limit)
    account_rows: list[dict[str, Any]] = []
    content_items: list[ContentItem] = []

    for idx, source in enumerate(sources, start=1):
        name = source_name(source)
        homepage = source_url(source)
        if not homepage:
            account_rows.append({
                "account_name": name,
                "homepage_url": "",
                "status": "needs_url",
                "failure_reason": "配置中缺少抖音主页链接",
                "video_ids": [],
                "resolved_items": 0,
            })
            continue
        raw_path = raw_dir / f"homepage_{idx}.html"
        html, fetch_status = fetch_homepage(homepage, raw_path)
        video_ids = extract_video_ids(html)
        status, reason = diagnose_homepage(html, fetch_status)
        if video_ids:
            status = "success"
            reason = "公开主页 HTML 中发现作品 ID；已用现有单条 URL resolver 做浅层解析。"
            items = resolve_discovered_videos(video_ids, args.video_limit)
            for item in items:
                if not item.account_name:
                    item.account_name = name
            content_items.extend(items)
        account_rows.append({
            "account_name": name,
            "homepage_url": homepage,
            "source_role": source_role(source),
            "fetch_status": fetch_status,
            "status": status,
            "failure_reason": reason,
            "video_ids": video_ids[: args.video_limit],
            "resolved_items": len(video_ids[: args.video_limit]),
            "raw_payload_path": str(raw_path),
            "risk_boundary": "低频只读；不保存cookie/token/profile；不抓评论；不下载视频。",
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "account_probe_results.csv", account_rows)
    (out_dir / "account_probe_results.json").write_text(json.dumps(account_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "content_items.jsonl").open("w", encoding="utf-8") as handle:
        for item in content_items:
            handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")

    print(json.dumps({
        "ok": True,
        "accounts": len(account_rows),
        "content_items": len(content_items),
        "output": str(out_dir),
        "summary": [
            {
                "account_name": row["account_name"],
                "status": row["status"],
                "resolved_items": row["resolved_items"],
                "failure_reason": row["failure_reason"],
            }
            for row in account_rows
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
