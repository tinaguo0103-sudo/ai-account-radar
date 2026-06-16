#!/usr/bin/env python3
"""Explicit Douyin ASR transcription with cost guards.

Default mode is dry-run. To spend ASR quota, the caller must pass --yes and
either --confirm-free-quota or a configured price/cost cap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from local_env import load_local_env

import url_content_resolver as resolver


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "spikes" / "douyin_transcripts"
LOCAL_DOUYIN_PY = ROOT / ".venv-douyin" / "bin" / "python"
MANUAL_JSONL = OUT_DIR / "transcribed_content_items.jsonl"


def first_url(value) -> str:
    if isinstance(value, dict):
        urls = value.get("url_list")
        if isinstance(urls, list) and urls:
            return str(urls[0])
        return str(value.get("url") or "")
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value or "")


def extract_item_from_raw(raw_path: Path) -> dict:
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    loader = data.get("loaderData", {})
    for key in ("video_(id)/page", "note_(id)/page"):
        if isinstance(loader.get(key), dict):
            info = loader[key].get("videoInfoRes")
            if info and info.get("item_list"):
                return info["item_list"][0]
    raise RuntimeError("raw payload missing item_list")


def metadata_from_raw_payload(raw_path: Path, source_url: str = "") -> dict:
    douyin_item = extract_item_from_raw(raw_path)
    video = douyin_item.get("video") or {}
    author = douyin_item.get("author") or {}
    video_url = first_url((video.get("play_addr") or {}).get("url_list")).replace("playwm", "play")
    video_id = str(douyin_item.get("aweme_id") or douyin_item.get("group_id") or "")
    title = str(douyin_item.get("desc") or douyin_item.get("share_info", {}).get("share_title") or "")
    account_name = str(author.get("nickname") or author.get("unique_id") or "")
    return {
        "title": title or "未命名抖音视频",
        "video_id": video_id,
        "video_url": video_url,
        "duration_seconds": float(video.get("duration") or 0) / 1000.0,
        "raw_payload_path": str(raw_path),
        "account_name": account_name,
        "source_url": source_url or (f"https://www.douyin.com/video/{video_id}" if video_id else ""),
    }


def extract_video_metadata(url: str, raw_dir: Path) -> dict:
    items = resolver.resolve_douyin(url, raw_dir)
    item = items[0]
    if item.fetch_status != "success":
        raise RuntimeError(item.failure_reason or "douyin resolver failed")
    raw = Path(item.raw_payload_path)
    metadata = metadata_from_raw_payload(raw, url)
    metadata["content_item"] = item.__dict__
    if item.content_title:
        metadata["title"] = item.content_title
    return metadata


def transcribe_video_url(video_url: str, model: str, api_key: str, timeout: int) -> str:
    if not LOCAL_DOUYIN_PY.exists():
        raise RuntimeError(".venv-douyin/bin/python not found; install douyin-mcp-server environment first")
    code = """
import json
import sys
from douyin_mcp_server.douyin_processor import DouyinProcessor

video_url = sys.argv[1]
model = sys.argv[2]
api_key = sys.argv[3]
processor = DouyinProcessor(api_key, model)
text = processor.extract_text_from_video_url(video_url)
print(json.dumps({"ok": True, "text": text}, ensure_ascii=False))
"""
    proc = subprocess.run(
        [str(LOCAL_DOUYIN_PY), "-c", code, video_url, model, api_key],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout)[-2000:])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "ASR returned not ok")
    return str(payload.get("text") or "")


def transcript_text_from_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def content_fingerprint(metadata: dict, text: str) -> str:
    seed = "|".join([
        "douyin_transcript",
        str(metadata.get("source_url") or ""),
        str(metadata.get("video_id") or ""),
        str(metadata.get("title") or ""),
        str(len(text)),
    ])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def write_transcript_content_item(metadata: dict, text: str, model: str, transcript_path: Path | None = None) -> Path:
    MANUAL_JSONL.parent.mkdir(parents=True, exist_ok=True)
    raw_payload_path = str(metadata.get("raw_payload_path") or "")
    source_url = str(metadata.get("source_url") or "")
    row = {
        "来源类型": "对标视频",
        "平台": "抖音",
        "账号名/公众号名": metadata.get("account_name") or "",
        "内容标题": metadata.get("title") or "未命名抖音视频",
        "内容链接": source_url,
        "内容形态": "短视频转写",
        "封面文字": metadata.get("title") or "",
        "正文/字幕/简介片段": text,
        "发布时间": "",
        "评论区问题": "",
        "截图/OCR文本": raw_payload_path,
        "抓取方式": "douyin_paraformer_transcript",
        "抓取状态": "ok",
        "失败原因": "",
        "内容指纹": content_fingerprint(metadata, text),
        "正文原始长度": str(len(text)),
        "正文是否截断": "否",
        "解析说明": f"P1口播转写：ASR模型{model}；不含评论区和画面OCR。原始payload路径：{raw_payload_path}；转写文件：{transcript_path or ''}",
    }
    existing: list[dict] = []
    if MANUAL_JSONL.exists():
        for line in MANUAL_JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            if old.get("内容指纹") != row["内容指纹"] and old.get("内容链接") != row["内容链接"]:
                existing.append(old)
    existing.append(row)
    MANUAL_JSONL.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in existing) + "\n", encoding="utf-8")
    return MANUAL_JSONL


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description="Explicit Douyin ASR transcription. Default is dry-run.")
    parser.add_argument("--url", default="", help="Douyin video/share URL. Not required when --raw-payload has a source URL or --video-url is provided.")
    parser.add_argument("--raw-payload", default="", help="Use raw resolver payload from CDP probe instead of re-opening Douyin.")
    parser.add_argument("--video-url", default="", help="Direct playable video URL. Skips Douyin page metadata resolution.")
    parser.add_argument("--source-url", default="", help="Canonical Douyin source URL when using --raw-payload/--video-url.")
    parser.add_argument("--title", default="", help="Override title when using --video-url.")
    parser.add_argument("--account-name", default="", help="Override account name when using --video-url.")
    parser.add_argument("--video-id", default="", help="Override video id when using --video-url.")
    parser.add_argument("--transcript-file", default="", help="Convert an existing transcript file into ContentItem without calling ASR.")
    parser.add_argument("--model", default=os.getenv("DOUYIN_ASR_MODEL", "paraformer-v2"))
    parser.add_argument("--max-minutes", type=float, default=float(os.getenv("DOUYIN_ASR_MAX_SINGLE_MINUTES", "15")))
    parser.add_argument("--max-cost-yuan", type=float, default=float(os.getenv("DOUYIN_ASR_MAX_COST_YUAN", "0") or "0"))
    parser.add_argument("--price-per-minute", type=float, default=float(os.getenv("DOUYIN_ASR_PRICE_PER_MINUTE", "0") or "0"))
    parser.add_argument("--confirm-free-quota", action="store_true", help="Confirm 百炼 free quota is enabled with use-up-stop.")
    parser.add_argument("--allow-long", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Actually call ASR. Without this, dry-run only.")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw_resolver"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.raw_payload:
        metadata = metadata_from_raw_payload(Path(args.raw_payload), args.source_url)
    elif args.video_url:
        metadata = {
            "title": args.title or "未命名抖音视频",
            "video_id": args.video_id,
            "video_url": args.video_url,
            "duration_seconds": 0.0,
            "raw_payload_path": "",
            "account_name": args.account_name,
            "source_url": args.source_url or args.url,
        }
    elif args.url:
        metadata = extract_video_metadata(args.url, raw_dir)
    else:
        raise SystemExit("Provide --url, --raw-payload, or --video-url.")
    if args.title:
        metadata["title"] = args.title
    if args.account_name:
        metadata["account_name"] = args.account_name
    if args.video_id:
        metadata["video_id"] = args.video_id
    if args.source_url:
        metadata["source_url"] = args.source_url
    duration_minutes = metadata["duration_seconds"] / 60.0
    estimated_cost = duration_minutes * args.price_per_minute if args.price_per_minute else None

    guard_failures: list[str] = []
    if duration_minutes > args.max_minutes and not args.allow_long:
        guard_failures.append(f"视频时长 {duration_minutes:.1f} 分钟超过单次上限 {args.max_minutes:.1f} 分钟")
    if not os.getenv("DASHSCOPE_API_KEY"):
        guard_failures.append("缺少 DASHSCOPE_API_KEY")
    if args.yes and not args.confirm_free_quota:
        if not args.price_per_minute or not args.max_cost_yuan:
            guard_failures.append("未确认免费额度，也未配置 price/max-cost，拒绝调用 ASR")
        elif estimated_cost is not None and estimated_cost > args.max_cost_yuan:
            guard_failures.append(f"预计成本 {estimated_cost:.4f} 元超过上限 {args.max_cost_yuan:.4f} 元")

    base = {
        "ok": not guard_failures,
        "dry_run": not args.yes,
        "model": args.model,
        "title": metadata["title"],
        "video_id": metadata["video_id"],
        "duration_minutes": round(duration_minutes, 2),
        "estimated_cost_yuan": None if estimated_cost is None else round(estimated_cost, 6),
        "confirm_free_quota": args.confirm_free_quota,
        "guard_failures": guard_failures,
        "raw_payload_path": metadata["raw_payload_path"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if args.transcript_file:
        transcript_path = Path(args.transcript_file)
        text = transcript_text_from_file(transcript_path)
        manual_path = write_transcript_content_item(metadata, text, args.model, transcript_path)
        payload = {**base, "ok": True, "dry_run": False, "used_existing_transcript": True, "transcript_length": len(text)}
        safe_id = metadata["video_id"] or "douyin"
        json_path = out_dir / f"{safe_id}_transcript_content_item.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "ok": True,
            "used_existing_transcript": True,
            "transcript_length": len(text),
            "content_item_jsonl": str(manual_path),
            "metadata": str(json_path),
        }, ensure_ascii=False, indent=2))
        return 0
    if guard_failures or not args.yes:
        path = out_dir / "last_transcribe_dry_run.json"
        path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({**base, "output": str(path)}, ensure_ascii=False, indent=2))
        return 1 if args.yes and guard_failures else 0

    if not metadata.get("video_url"):
        raise RuntimeError("missing playable video_url; use --raw-payload from CDP probe or --video-url")
    text = transcribe_video_url(metadata["video_url"], args.model, os.environ["DASHSCOPE_API_KEY"], args.timeout)
    payload = {**base, "ok": True, "transcript_length": len(text), "transcript": text}
    safe_id = metadata["video_id"] or "douyin"
    json_path = out_dir / f"{safe_id}_transcript.json"
    md_path = out_dir / f"{safe_id}_transcript.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(f"# {metadata['title']}\n\n{text}\n", encoding="utf-8")
    manual_path = write_transcript_content_item(metadata, text, args.model, md_path)
    print(json.dumps({
        "ok": True,
        "model": args.model,
        "duration_minutes": round(duration_minutes, 2),
        "transcript_length": len(text),
        "json": str(json_path),
        "markdown": str(md_path),
        "content_item_jsonl": str(manual_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
