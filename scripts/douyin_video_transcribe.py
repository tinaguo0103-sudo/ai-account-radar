#!/usr/bin/env python3
"""Explicit Douyin ASR transcription with cost guards.

Default mode is dry-run. To spend ASR quota, the caller must pass --yes and
either --confirm-free-quota or a configured price/cost cap.
"""
from __future__ import annotations

import argparse
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


def extract_video_metadata(url: str, raw_dir: Path) -> dict:
    items = resolver.resolve_douyin(url, raw_dir)
    item = items[0]
    if item.fetch_status != "success":
        raise RuntimeError(item.failure_reason or "douyin resolver failed")
    raw = Path(item.raw_payload_path)
    data = json.loads(raw.read_text(encoding="utf-8"))
    loader = data.get("loaderData", {})
    douyin_item = None
    for key in ("video_(id)/page", "note_(id)/page"):
        if isinstance(loader.get(key), dict):
            info = loader[key].get("videoInfoRes")
            if info and info.get("item_list"):
                douyin_item = info["item_list"][0]
                break
    if not douyin_item:
        raise RuntimeError("raw payload missing item_list")
    video = douyin_item.get("video") or {}
    play_url = resolver.first_url((video.get("play_addr") or {}).get("url_list", "")).replace("playwm", "play")
    return {
        "title": item.content_title,
        "video_id": str(douyin_item.get("aweme_id") or douyin_item.get("group_id") or ""),
        "video_url": play_url,
        "duration_seconds": float(video.get("duration") or 0) / 1000.0,
        "raw_payload_path": str(raw),
        "content_item": item.__dict__,
    }


def transcribe_with_douyin_mcp(url: str, model: str, api_key: str, timeout: int) -> str:
    if not LOCAL_DOUYIN_PY.exists():
        raise RuntimeError(".venv-douyin/bin/python not found; install douyin-mcp-server environment first")
    code = """
import json
import sys
from douyin_mcp_server.douyin_processor import DouyinProcessor

url = sys.argv[1]
model = sys.argv[2]
api_key = sys.argv[3]
processor = DouyinProcessor(api_key, model)
video_info = processor.parse_share_url(url)
text = processor.extract_text_from_video_url(video_info["url"])
print(json.dumps({"ok": True, "video_info": video_info, "text": text}, ensure_ascii=False))
"""
    proc = subprocess.run(
        [str(LOCAL_DOUYIN_PY), "-c", code, url, model, api_key],
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


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description="Explicit Douyin ASR transcription. Default is dry-run.")
    parser.add_argument("--url", required=True)
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
    metadata = extract_video_metadata(args.url, raw_dir)
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
    if guard_failures or not args.yes:
        path = out_dir / "last_transcribe_dry_run.json"
        path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({**base, "output": str(path)}, ensure_ascii=False, indent=2))
        return 1 if args.yes and guard_failures else 0

    text = transcribe_with_douyin_mcp(args.url, args.model, os.environ["DASHSCOPE_API_KEY"], args.timeout)
    payload = {**base, "ok": True, "transcript_length": len(text), "transcript": text}
    safe_id = metadata["video_id"] or "douyin"
    json_path = out_dir / f"{safe_id}_transcript.json"
    md_path = out_dir / f"{safe_id}_transcript.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(f"# {metadata['title']}\n\n{text}\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "model": args.model,
        "duration_minutes": round(duration_minutes, 2),
        "transcript_length": len(text),
        "json": str(json_path),
        "markdown": str(md_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
