#!/usr/bin/env python3
"""AR-020 local content-library replay and reverse evaluation.

Reads existing content_items.csv files from output/runs and output/dry_runs,
filters by collection date, then reuses content_sampler's scoring pipeline
without fetching external sources or writing Feishu.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import content_sampler
import topic_flow_rework as flow


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output" / "topic_replay"


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
    reverse_rows = flow.reverse_evaluation_rows(selected, candidates, item_by_fp)
    return {
        "items": item_rows,
        "breakdowns": breakdown_rows,
        "candidates": candidates,
        "selected": selected,
        "reverse_rows": reverse_rows,
        "breakdown_by_fp": breakdown_by_fp,
        "item_by_fp": item_by_fp,
    }


def write_outputs(result: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    content_sampler.write_csv(out_dir / "replay_content_items.csv", result["items"])
    content_sampler.write_csv(out_dir / "replay_candidates.csv", result["candidates"])
    content_sampler.write_csv(out_dir / "replay_selected_topics.csv", result["selected"])
    flow.write_reverse_evaluation(out_dir / "reverse_topic_evaluation.csv", result["reverse_rows"])
    return {
        "content_items": str(out_dir / "replay_content_items.csv"),
        "candidates": str(out_dir / "replay_candidates.csv"),
        "selected_topics": str(out_dir / "replay_selected_topics.csv"),
        "reverse_topic_evaluation": str(out_dir / "reverse_topic_evaluation.csv"),
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
    summary = {
        "ok": True,
        "since": args.since,
        "input_files": [str(path) for path in csv_paths if path.exists()],
        "content_items": len(items),
        "candidate_count": len(result["candidates"]),
        "selected_count": len(result["selected"]),
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
