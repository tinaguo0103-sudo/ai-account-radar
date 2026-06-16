#!/usr/bin/env python3
"""Rank Douyin homepage probe videos before spending ASR quota.

This script reads local CDP probe outputs and produces a transcript candidate
list. It does not call ASR, does not write Feishu, and does not download media.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROBE_DIR = ROOT / "output" / "spikes" / "douyin_cdp_source_watch_probe"
DEFAULT_OUT_DIR = ROOT / "output" / "spikes" / "douyin_transcript_candidates"


POSITIVE_KEYWORDS = {
    "strong": [
        "教程", "全流程", "工作流", "复盘", "拆解", "案例", "实战", "保姆级", "从0到1",
        "Claude Code", "Codex", "Agent", "智能体", "AI视频", "短片", "分镜", "镜头",
        "小云雀", "Runway", "Seedance", "Kling", "Luma", "Excel", "PPT", "飞书",
        "品牌", "汽车", "营销", "内容团队", "获客", "投放",
    ],
    "medium": [
        "玩法", "方法", "效率", "自动化", "模板", "工具", "生成", "提示词", "脚本",
        "生图", "剪辑", "口播", "AI", "人工智能",
    ],
}
NEGATIVE_KEYWORDS = ["直播预告", "抽奖", "招聘", "日常", "碎碎念", "纯娱乐", "转发"]


@dataclass
class VideoCandidate:
    account_name: str
    video_id: str
    title: str
    url: str
    duration_seconds: float
    duration_minutes: float
    score: int
    decision: str
    transcript_mode: str
    reason: str
    estimated_cost_yuan: str
    raw_payload_path: str


def first_url(value: Any) -> str:
    if isinstance(value, dict):
        urls = value.get("url_list")
        if isinstance(urls, list) and urls:
            return str(urls[0])
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value or "")


def extract_item(raw_path: Path) -> dict[str, Any] | None:
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    loader = data.get("loaderData", {})
    for key in ("video_(id)/page", "note_(id)/page"):
        if isinstance(loader.get(key), dict):
            info = loader[key].get("videoInfoRes")
            if info and info.get("item_list"):
                return info["item_list"][0]
    return None


def raw_files_by_video_id(raw_dir: Path) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for raw in raw_dir.glob("douyin_*.json"):
        try:
            item = extract_item(raw)
        except Exception:
            continue
        if not item:
            continue
        video_id = str(item.get("aweme_id") or item.get("group_id") or "")
        if video_id:
            mapping[video_id] = raw
    return mapping


def load_probe_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rows", []) if isinstance(data, dict) else []


def video_id_from_url(url: str) -> str:
    match = re.search(r"(?:/video/|modal_id=)(\d{10,})", url)
    return match.group(1) if match else ""


def score_title(title: str) -> tuple[int, list[str]]:
    score = 0
    hits: list[str] = []
    for word in POSITIVE_KEYWORDS["strong"]:
        if word.lower() in title.lower():
            score += 2
            hits.append(word)
    for word in POSITIVE_KEYWORDS["medium"]:
        if word.lower() in title.lower():
            score += 1
            hits.append(word)
    for word in NEGATIVE_KEYWORDS:
        if word in title:
            score -= 3
            hits.append(f"负向:{word}")
    return score, hits[:8]


def estimate_cost(duration_minutes: float, price_per_minute: float | None) -> str:
    if price_per_minute is None:
        return "未配置单价；按百炼控制台免费额度/用完即停保护"
    return f"{duration_minutes * price_per_minute:.4f}"


def decide(score: int, duration_minutes: float, max_single_minutes: float, manual_confirm_minutes: float) -> tuple[str, str]:
    if duration_minutes >= manual_confirm_minutes:
        return "需人工确认", "long_partial_or_skip"
    if score >= 5 and duration_minutes <= max_single_minutes:
        return "建议转写", "full"
    if score >= 4 and duration_minutes <= manual_confirm_minutes:
        return "可选转写", "full_or_first_10_min"
    if score >= 3 and duration_minutes <= max_single_minutes:
        return "备选观察", "skip_by_default"
    return "暂不转写", "skip"


def apply_suggestion_cap(candidates: list[VideoCandidate], max_suggested: int) -> None:
    suggested = [row for row in candidates if row.decision == "建议转写"]
    for row in suggested[max_suggested:]:
        row.decision = "可选转写"
        row.transcript_mode = "full_or_first_10_min"
        row.reason = f"{row.reason}；超过每日建议转写上限，降为可选"


def build_candidates(args: argparse.Namespace) -> list[VideoCandidate]:
    probe_dir = Path(args.probe_dir)
    raw_map = raw_files_by_video_id(probe_dir / "raw_resolver")
    rows = load_probe_rows(probe_dir / "cdp_probe_results.json")
    price = float(args.price_per_minute) if args.price_per_minute else None

    candidates: list[VideoCandidate] = []
    for row in rows:
        account = row.get("account_name", "")
        for url in row.get("video_links", []) or []:
            video_id = video_id_from_url(url)
            raw = raw_map.get(video_id)
            if not raw:
                continue
            item = extract_item(raw)
            if not item:
                continue
            video = item.get("video") or {}
            duration_seconds = float(video.get("duration") or 0) / 1000.0
            duration_minutes = duration_seconds / 60.0
            title = str(item.get("desc", "")).strip()
            score, hits = score_title(title)
            decision, mode = decide(score, duration_minutes, args.max_single_minutes, args.manual_confirm_minutes)
            reason_parts = [
                f"命中关键词：{'、'.join(hits) if hits else '无'}",
                f"时长：{duration_minutes:.1f} 分钟",
            ]
            if duration_minutes >= args.manual_confirm_minutes:
                reason_parts.append("长视频，先人工确认是否只转前 5-10 分钟")
            elif decision == "建议转写":
                reason_parts.append("标题足够指向教程/流程/复盘，适合补口播全文")
            elif decision == "暂不转写":
                reason_parts.append("仅凭标题不足以支撑转写成本")
            candidates.append(VideoCandidate(
                account_name=account,
                video_id=video_id,
                title=title,
                url=url,
                duration_seconds=duration_seconds,
                duration_minutes=duration_minutes,
                score=score,
                decision=decision,
                transcript_mode=mode,
                reason="；".join(reason_parts),
                estimated_cost_yuan=estimate_cost(duration_minutes, price),
                raw_payload_path=str(raw),
            ))
    candidates.sort(key=lambda item: (
        {"建议转写": 0, "可选转写": 1, "备选观察": 2, "需人工确认": 3, "暂不转写": 4}.get(item.decision, 9),
        -item.score,
        item.duration_minutes,
    ))
    apply_suggestion_cap(candidates, args.max_suggested)
    candidates.sort(key=lambda item: (
        {"建议转写": 0, "可选转写": 1, "备选观察": 2, "需人工确认": 3, "暂不转写": 4}.get(item.decision, 9),
        -item.score,
        item.duration_minutes,
    ))
    return candidates


def write_csv(path: Path, rows: list[VideoCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(VideoCandidate.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def write_md(path: Path, rows: list[VideoCandidate], args: argparse.Namespace) -> None:
    lines = [
        "# 抖音视频转写候选",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 默认 ASR 模型：{args.model}",
        f"- 单条建议转写上限：{args.max_single_minutes} 分钟",
        f"- 长视频人工确认阈值：{args.manual_confirm_minutes} 分钟",
        f"- 单价配置：{args.price_per_minute or '未配置；优先使用百炼免费额度/用完即停'}",
        "",
        "| 决策 | 账号 | 时长 | 分数 | 标题 | 原因 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.decision} | {row.account_name} | {row.duration_minutes:.1f} | {row.score} | "
            f"{row.title.replace('|', '/')} | {row.reason.replace('|', '/')} |"
        )
    lines.extend([
        "",
        "## 使用建议",
        "",
        "- 先看 `建议转写`，每天最多挑 1-2 条。",
        "- `需人工确认` 多半是长视频，建议先人工判断是否只转前 5-10 分钟。",
        "- 本脚本不调用 ASR，不消耗百炼额度。",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description="Rank Douyin videos before ASR transcription.")
    parser.add_argument("--probe-dir", default=str(DEFAULT_PROBE_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--model", default=os.getenv("DOUYIN_ASR_MODEL", "paraformer-v2"))
    parser.add_argument("--price-per-minute", default=os.getenv("DOUYIN_ASR_PRICE_PER_MINUTE", ""))
    parser.add_argument("--max-single-minutes", type=float, default=float(os.getenv("DOUYIN_ASR_MAX_SINGLE_MINUTES", "15")))
    parser.add_argument("--manual-confirm-minutes", type=float, default=float(os.getenv("DOUYIN_ASR_MANUAL_CONFIRM_MINUTES", "30")))
    parser.add_argument("--max-suggested", type=int, default=int(os.getenv("DOUYIN_ASR_MAX_SUGGESTED_PER_RUN", "2")))
    args = parser.parse_args()

    rows = build_candidates(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "transcript_candidates.csv", rows)
    write_md(out_dir / "transcript_candidates.md", rows, args)
    print(json.dumps({
        "ok": True,
        "candidates": len(rows),
        "suggested_transcribe": sum(1 for row in rows if row.decision == "建议转写"),
        "optional_transcribe": sum(1 for row in rows if row.decision == "可选转写"),
        "needs_manual": sum(1 for row in rows if row.decision == "需人工确认"),
        "output_csv": str(out_dir / "transcript_candidates.csv"),
        "output_md": str(out_dir / "transcript_candidates.md"),
        "asr_model": args.model,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
