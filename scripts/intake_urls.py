#!/usr/bin/env python3
"""Convert pasted URLs into manual ContentItem JSONL input.

The script only uses public page parsing. It does not bypass login, CAPTCHA or
anti-scraping controls. If a URL cannot be parsed, it records the failure so
the pipeline can continue.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import content_sampler as sampler


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "manual" / "url_intake.jsonl"


def normalize_url(line: str) -> str:
    line = line.strip()
    if not line or line.startswith("#"):
        return ""
    match = re.search(r"https?://\S+", line)
    return match.group(0).rstrip("，,。)")


def classify_url(url: str) -> dict[str, str]:
    lower = url.lower()
    if "mp.weixin.qq.com" in lower:
        return {"来源类型": "公众号文章", "平台": "微信公众号", "内容形态": "长文"}
    if "xiaohongshu.com" in lower or "xhslink.com" in lower:
        return {"来源类型": "对标视频", "平台": "小红书", "内容形态": "图文/视频"}
    if "douyin.com" in lower or "iesdouyin.com" in lower:
        return {"来源类型": "对标视频", "平台": "抖音", "内容形态": "短视频"}
    if "weixin.qq.com/sph" in lower or "channels.weixin.qq.com" in lower:
        return {"来源类型": "对标视频", "平台": "视频号", "内容形态": "短视频"}
    if "aihot.virxact.com" in lower:
        return {"来源类型": "AIHOT热点", "平台": "AIHOT", "内容形态": "热点条目"}
    return {"来源类型": "公众号文章", "平台": "公开网页", "内容形态": "长文"}


def item_to_json(item: sampler.ContentItem) -> dict[str, str]:
    return {
        "来源类型": item.source_type,
        "平台": item.platform,
        "账号名/公众号名": item.account_name,
        "内容标题": item.title,
        "内容链接": item.url,
        "内容形态": item.content_shape,
        "封面文字": item.cover_text,
        "正文/字幕/简介片段": item.body_snippet,
        "发布时间": item.published_at,
        "评论区问题": item.comment_questions,
        "截图/OCR文本": item.ocr_text,
        "抓取方式": item.fetch_method,
        "抓取状态": item.fetch_status,
        "失败原因": item.failure_reason,
    }


def parse_url(url: str) -> dict[str, str]:
    meta = classify_url(url)
    fallback: dict[str, Any] = {
        "来源类型": meta["来源类型"],
        "平台": meta["平台"],
        "账号名/公众号名": "",
        "内容标题": "",
        "内容链接": url,
        "内容形态": meta["内容形态"],
    }
    if meta["来源类型"] == "对标视频":
        item = sampler.extract_video_shallow(url, fallback)
    else:
        item = sampler.extract_article(url, fallback)
        item.source_type = meta["来源类型"]
        item.platform = meta["平台"]
        item.content_shape = meta["内容形态"]
    return item_to_json(item)


def read_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        url = normalize_url(line)
        if url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse pasted URLs into JSONL manual content items.")
    parser.add_argument("urls_file", help="Text file with one or more URLs.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSONL path.")
    parser.add_argument("--append", action="store_true", help="Append to output instead of replacing it.")
    args = parser.parse_args()

    urls = read_urls(Path(args.urls_file))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for url in urls:
        try:
            rows.append(parse_url(url))
        except Exception as exc:
            meta = classify_url(url)
            rows.append({
                "来源类型": meta["来源类型"],
                "平台": meta["平台"],
                "账号名/公众号名": "",
                "内容标题": "URL解析失败",
                "内容链接": url,
                "内容形态": meta["内容形态"],
                "封面文字": "",
                "正文/字幕/简介片段": "",
                "发布时间": "",
                "评论区问题": "",
                "截图/OCR文本": "",
                "抓取方式": "url_intake",
                "抓取状态": "failed",
                "失败原因": f"{exc.__class__.__name__}: {exc}",
            })

    mode = "a" if args.append else "w"
    with out.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(json.dumps({
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "urls": len(urls),
        "output": str(out),
        "status": {status: sum(1 for row in rows if row["抓取状态"] == status) for status in sorted({row["抓取状态"] for row in rows})},
        "items": [{"title": row["内容标题"], "platform": row["平台"], "status": row["抓取状态"], "url": row["内容链接"]} for row in rows],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
