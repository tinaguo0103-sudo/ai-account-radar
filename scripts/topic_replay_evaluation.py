#!/usr/bin/env python3
"""AR-020 local content-library replay and reverse evaluation.

Reads existing content_items.csv files from output/runs and output/dry_runs,
filters by collection date, then reuses content_sampler's scoring pipeline
without fetching external sources or writing Feishu.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import content_sampler
import topic_flow_rework as flow


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output" / "topic_replay"
ACTIONABLE_ACTIONS = {"生成脚本包", "立即蹭热点"}
GENERIC_QUALITY_PATTERNS = flow.GENERIC_TRANSLATION_PATTERNS + [
    "把原内容转成 Austin 自己的",
    "真实业务场景",
]


def parse_day(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def discover_content_csvs(paths: list[str]) -> list[Path]:
    if paths:
        return [Path(path).expanduser() for path in paths]
    roots = [ROOT / "output" / "runs", ROOT / "output" / "dry_runs"]
    found: list[Path] = []
    for base in roots:
        if base.exists():
            found.extend(sorted(base.glob("*/content_items.csv")))
    return found


def row_date(row: dict[str, Any], fallback_path: Path) -> date | None:
    for field in ["运行日期", "最近采样日期", "采集时间", "发布时间"]:
        parsed = parse_day(str(row.get(field, "")))
        if parsed:
            return parsed
    for part in fallback_path.parts:
        if part.startswith("run_") and len(part) >= 12:
            try:
                return date.fromisoformat(f"{part[4:8]}-{part[8:10]}-{part[10:12]}")
            except ValueError:
                continue
    return None


def load_items(csv_paths: list[Path], since: date) -> list[content_sampler.ContentItem]:
    rows: list[dict[str, Any]] = []
    for path in csv_paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                day = row_date(row, path)
                if day and day >= since:
                    rows.append(row)
    items = [content_sampler.content_item_from_row(row) for row in rows]
    deduped: dict[str, content_sampler.ContentItem] = {}
    for item in items:
        if flow.is_quarantined_source(item):
            continue
        deduped.setdefault(item.fingerprint, item)
    return list(deduped.values())


def replay(items: list[content_sampler.ContentItem]) -> dict[str, Any]:
    item_rows = [content_sampler.item_row(item) for item in items]
    breakdown_rows = [content_sampler.breakdown(item) for item in items]
    item_by_fp = {item.fingerprint: item for item in items}
    breakdown_by_fp = {row["内容指纹"]: row for row in breakdown_rows}
    candidates = [
        content_sampler.topic_from_breakdown(row, item_by_fp[row["内容指纹"]])
        for row in breakdown_rows
        if row["是否进入候选初筛"] == "是"
    ]
    candidates = content_sampler.apply_editorial_judgement(candidates, item_by_fp)
    selected = content_sampler.select_skill_review_candidates(candidates)
    selected = content_sampler.assign_action_quotas(selected)
    selected = content_sampler.apply_editorial_judgement(selected, item_by_fp)
    selected = content_sampler.assign_today_priority(selected)
    reverse_rows = flow.reverse_evaluation_rows(
        selected,
        candidates,
        item_by_fp,
        max_selected=content_sampler.MAX_SKILL_REVIEW_CANDIDATES,
    )
    return {
        "items": item_rows,
        "breakdowns": breakdown_rows,
        "candidates": candidates,
        "selected": selected,
        "reverse_rows": reverse_rows,
        "breakdown_by_fp": breakdown_by_fp,
        "item_by_fp": item_by_fp,
    }


def is_actionable_topic(row: dict[str, Any]) -> bool:
    return (
        row.get("推荐动作") in ACTIONABLE_ACTIONS
        and row.get("是否建议进入制作") == "是"
        and row.get("AI味风险") != "高"
        and row.get("Austin转译质量") in {"具体可转译", "需补重大性落地证据"}
    )


def is_observe_topic(row: dict[str, Any]) -> bool:
    return not is_actionable_topic(row)


def quality_flag_reasons(row: dict[str, Any], theme_counts: collections.Counter[str]) -> list[str]:
    reasons: list[str] = []
    translation = str(row.get("Austin转译角度") or row.get("对标转译角度") or row.get("我的蹭热点角度") or "")
    if any(pattern in translation for pattern in GENERIC_QUALITY_PATTERNS):
        reasons.append("转译解释仍偏模板化或缺少来源特异性")
    if row.get("Austin转译质量") not in {"具体可转译", "需补重大性落地证据"}:
        reasons.append(row.get("Austin转译质量原因") or "转译证据不足")
    if row.get("来源类型") == "AIHOT热点" and row.get("推荐动作") in ACTIONABLE_ACTIONS and not row.get("AIHOT重大性说明"):
        reasons.append("AI Hot 入选但缺重大性说明")
    theme = str(row.get("主题簇") or "未分簇")
    if theme_counts[theme] > 2:
        reasons.append(f"主题簇重复较多：{theme_counts[theme]} 条，需要 PM 判断是否合并或保留最强候选")
    if row.get("是否建议进入制作") != "是" and row.get("推荐动作") in ACTIONABLE_ACTIONS:
        reasons.append("推荐动作和主编建议不一致，不能按可行动候选处理")
    if row.get("是否建议进入制作") == "暂存观察" and row.get("降级原因"):
        reasons.append(f"暂存原因：{row.get('降级原因')}")
    return reasons


def pm_quality_rows(selected: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    theme_counts: collections.Counter[str] = collections.Counter(row.get("主题簇") or "未分簇" for row in selected)
    actionable: list[dict[str, Any]] = []
    observe: list[dict[str, Any]] = []
    ai_hot: list[dict[str, Any]] = []
    quality_flags: list[dict[str, Any]] = []
    for row in selected:
        status = "actionable" if is_actionable_topic(row) else "observe"
        reasons = quality_flag_reasons(row, theme_counts)
        report_row = {
            "PM分层": "可行动候选" if status == "actionable" else "暂存观察/弱证据",
            "主题簇": row.get("主题簇", ""),
            "同簇数量": str(theme_counts[row.get("主题簇") or "未分簇"]),
            "推荐动作": row.get("推荐动作", ""),
            "是否建议进入制作": row.get("是否建议进入制作", ""),
            "来源类型": row.get("来源类型", ""),
            "原始来源账号": row.get("原始来源账号", row.get("账号名/公众号名", "")),
            "原始来源标题": row.get("原始来源标题", row.get("来源内容", "")),
            "Austin映射方向": row.get("Austin映射方向", row.get("对应方向", "")),
            "Austin转译角度": row.get("Austin转译角度", row.get("对标转译角度", "")),
            "Austin转译质量": row.get("Austin转译质量", ""),
            "质量/降级说明": "；".join(reasons) or "证据和动作暂未发现明显冲突",
            "编辑判断分": row.get("编辑判断分", ""),
            "人设匹配分": row.get("人设匹配分", ""),
            "AIHOT重大性说明": row.get("AIHOT重大性说明", ""),
            "内容指纹": row.get("内容指纹", ""),
        }
        if status == "actionable":
            actionable.append(report_row)
        else:
            observe.append(report_row)
        if row.get("来源类型") == "AIHOT热点":
            ai_hot.append(report_row)
        if reasons:
            quality_flags.append(report_row)
    return {
        "actionable": actionable,
        "observe": observe,
        "ai_hot": ai_hot,
        "quality_flags": quality_flags,
    }


def write_pm_quality_report(result: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    rows = pm_quality_rows(result["selected"])
    content_sampler.write_csv(out_dir / "pm_actionable_topics.csv", rows["actionable"])
    content_sampler.write_csv(out_dir / "pm_observe_topics.csv", rows["observe"])
    content_sampler.write_csv(out_dir / "pm_aihot_selected_topics.csv", rows["ai_hot"])
    content_sampler.write_csv(out_dir / "pm_selected_quality_flags.csv", rows["quality_flags"])
    theme_counts = collections.Counter(row.get("主题簇") or "未分簇" for row in result["selected"])
    reverse_flags = [row for row in result["reverse_rows"] if row.potentially_better]
    lines = [
        "# AR-020 PM Editorial Quality Report",
        "",
        "这份报告只用于 PM/QA 判断选题质量；不写飞书、不发卡。",
        "",
        "## Summary",
        f"- Selected review pool: {len(result['selected'])}",
        f"- Actionable candidates: {len(rows['actionable'])}",
        f"- Observe / weak-evidence rows: {len(rows['observe'])}",
        f"- AI Hot selected rows: {len(rows['ai_hot'])}",
        f"- Selected quality flags: {len(rows['quality_flags'])}",
        f"- Reverse high-fit missed flags: {len(reverse_flags)}",
        "",
        "## Theme Clusters",
    ]
    for theme, count in theme_counts.most_common():
        lines.append(f"- {theme}: {count}")
    lines.extend(["", "## Actionable Candidates"])
    for row in rows["actionable"][:12]:
        lines.extend([
            f"- {row['原始来源标题'][:80]}",
            f"  - 来源/方向：{row['来源类型']} / {row['Austin映射方向']} / {row['主题簇']}",
            f"  - 转译：{row['Austin转译角度']}",
        ])
    if not rows["actionable"]:
        lines.append("- 无：本轮 replay 只形成观察池，不能当作可发布候选质量通过。")
    lines.extend(["", "## Observe Rows"])
    for row in rows["observe"][:12]:
        lines.extend([
            f"- {row['原始来源标题'][:80]}",
            f"  - 原因：{row['质量/降级说明']}",
        ])
    lines.extend(["", "## AI Hot Rows"])
    for row in rows["ai_hot"][:8]:
        lines.extend([
            f"- {row['原始来源标题'][:80]}",
            f"  - 重大性/角度：{row['AIHOT重大性说明'] or row['Austin转译角度']}",
        ])
    lines.extend(["", "## Quality Flags"])
    for row in rows["quality_flags"][:12]:
        lines.extend([
            f"- {row['原始来源标题'][:80]}",
            f"  - 风险：{row['质量/降级说明']}",
        ])
    (out_dir / "pm_editorial_quality_report.md").write_text("\n".join(lines), encoding="utf-8")
    return {
        "actionable_count": len(rows["actionable"]),
        "observe_count": len(rows["observe"]),
        "aihot_selected_count": len(rows["ai_hot"]),
        "selected_quality_flag_count": len(rows["quality_flags"]),
        "theme_clusters": dict(theme_counts),
        "outputs": {
            "pm_editorial_quality_report": str(out_dir / "pm_editorial_quality_report.md"),
            "pm_actionable_topics": str(out_dir / "pm_actionable_topics.csv"),
            "pm_observe_topics": str(out_dir / "pm_observe_topics.csv"),
            "pm_aihot_selected_topics": str(out_dir / "pm_aihot_selected_topics.csv"),
            "pm_selected_quality_flags": str(out_dir / "pm_selected_quality_flags.csv"),
        },
    }


def write_outputs(result: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    content_sampler.write_csv(out_dir / "replay_content_items.csv", result["items"])
    content_sampler.write_csv(out_dir / "replay_candidates.csv", result["candidates"])
    content_sampler.write_csv(out_dir / "replay_selected_topics.csv", result["selected"])
    flow.write_reverse_evaluation(out_dir / "reverse_topic_evaluation.csv", result["reverse_rows"])
    quality = write_pm_quality_report(result, out_dir)
    result["pm_quality"] = quality
    return {
        "content_items": str(out_dir / "replay_content_items.csv"),
        "candidates": str(out_dir / "replay_candidates.csv"),
        "selected_topics": str(out_dir / "replay_selected_topics.csv"),
        "reverse_topic_evaluation": str(out_dir / "reverse_topic_evaluation.csv"),
        **quality["outputs"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AR-020 replay local content library after a given date.")
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--content-csv", action="append", default=[], help="Specific content_items.csv path. Can be repeated.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    since = date.fromisoformat(args.since)
    csv_paths = discover_content_csvs(args.content_csv)
    items = load_items(csv_paths, since)
    result = replay(items)
    outputs = write_outputs(result, Path(args.out_dir))
    quality = result["pm_quality"]
    summary = {
        "ok": True,
        "since": args.since,
        "input_files": [str(path) for path in csv_paths if path.exists()],
        "content_items": len(items),
        "candidate_count": len(result["candidates"]),
        "selected_count": len(result["selected"]),
        "actionable_count": quality["actionable_count"],
        "observe_count": quality["observe_count"],
        "aihot_selected_count": quality["aihot_selected_count"],
        "selected_quality_flag_count": quality["selected_quality_flag_count"],
        "theme_clusters": quality["theme_clusters"],
        "source_composition": flow.source_composition(result["selected"]),
        "reverse_flags": sum(1 for row in result["reverse_rows"] if row.potentially_better),
        "outputs": outputs,
        "writes_feishu": False,
    }
    (Path(args.out_dir) / "topic_replay_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
