#!/usr/bin/env python3
"""AR-020B real Skill replay for 2026-07-01+ content libraries.

This tool does not write Feishu. Deterministic code only builds a broad review
pool and source-evidence context; the ai-account-editorial-director Skill owns
the user-visible topic fields.
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
import editorial_skill_runner
import topic_field_contract as field_contract
import topic_flow_rework as flow
import topic_replay_evaluation as deterministic_replay


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path("/private/tmp/ar020b_skill_replay")
SAMPLE_KEYWORDS = {
    "knowledge_base": ["Codex", "Obsidian", "知识库", "RAG"],
    "ai_director": ["AIGC", "分镜", "故事板", "AI视频", "短剧"],
    "tooling_skill": ["Mx-Shell", "Skill", "Claude Code", "Codex"],
    "technical_automation": ["CI/CD", "Shell", "自动化", "大伟聊前端"],
    "broad_aihot_or_growth": ["企业", "增长", "AI Hot", "融资", "行业"],
}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_pre_skill_pool(items: list[content_sampler.ContentItem], max_candidates: int) -> dict[str, Any]:
    item_by_fp = {item.fingerprint: item for item in items}
    breakdown_rows = [content_sampler.breakdown(item) for item in items]
    candidates = [
        content_sampler.topic_from_breakdown(row, item_by_fp[row["内容指纹"]])
        for row in breakdown_rows
        if row["是否进入候选初筛"] == "是"
    ]
    candidates = content_sampler.apply_editorial_judgement(candidates, item_by_fp)
    selected = content_sampler.select_skill_review_candidates(candidates)[:max_candidates]
    return {
        "items": [content_sampler.item_row(item) for item in items],
        "breakdowns": breakdown_rows,
        "candidates": candidates,
        "pre_skill_pool": selected,
        "item_by_fp": item_by_fp,
    }


def run_skill(pool: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, str]], dict[str, Any], str]:
    rows = [{key: str(value or "") for key, value in row.items()} for row in pool]
    if args.engine == "codex":
        enriched, meta = editorial_skill_runner.run_codex_skill(rows, args.codex_model, args.timeout)
        return enriched, meta, "codex"
    enriched = editorial_skill_runner.normalize_batch([editorial_skill_runner.enrich(row) for row in rows])
    return enriched, {"mode": "explicit_deterministic", "fallback_only": True, "not_editorial_quality": True}, "deterministic"


def classify_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    outputs = {
        "actionable": [],
        "observe": [],
        "rejected": [],
        "contract_failures": [],
        "fallback_rows": [],
    }
    guarded_rows = field_contract.apply_batch_quality_guards(rows)
    for row in guarded_rows:
        issues = field_contract.validate_field_contract(row)
        row_with_status = field_contract.mark_contract_result(row, issues)
        if row_with_status.get("fallback_only") == "true" or row_with_status.get("not_editorial_quality") == "true":
            outputs["fallback_rows"].append(row_with_status)
        if issues:
            outputs["contract_failures"].append(row_with_status)
        level = row_with_status.get("今日建议级别") or row_with_status.get("候选状态")
        if (
            str(row_with_status.get("推荐动作") or "") in field_contract.ACTIONABLE_ACTIONS
            and not issues
            and row_with_status.get("fallback_only") != "true"
            and row_with_status.get("not_editorial_quality") != "true"
        ):
            outputs["actionable"].append(row_with_status)
        elif level in {"暂存观察", "可选候选"}:
            outputs["observe"].append(row_with_status)
        else:
            outputs["rejected"].append(row_with_status)
    return outputs


def title_body_check_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checked = field_contract.apply_batch_quality_guards(rows)
    out: list[dict[str, Any]] = []
    for row in checked:
        out.append({
            "原始来源标题": row.get("原始来源标题") or row.get("来源内容", ""),
            "原始来源账号": row.get("原始来源账号") or row.get("账号名/公众号名", ""),
            "推荐动作": row.get("推荐动作", ""),
            "今日建议级别": row.get("今日建议级别") or row.get("候选状态", ""),
            "可发布标题": row.get("可发布标题", ""),
            "选题命题": row.get("选题命题") or row.get("选题标题", ""),
            "title_pattern_family": row.get("title_pattern_family", ""),
            "title_quality_status": row.get("title_quality_status", ""),
            "title_quality_issues": row.get("title_quality_issues", ""),
            "主编判断摘要": row.get("主编判断摘要", ""),
            "标题思路": row.get("标题思路", ""),
            "field_contract_status": row.get("field_contract_status", ""),
            "field_contract_issues": row.get("field_contract_issues", ""),
        })
    return out


def near_miss_rows(reverse_rows: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in reverse_rows:
        if getattr(row, "potentially_better", False):
            rows.append(row.__dict__)
    return rows


def sample_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sample_key, keywords in SAMPLE_KEYWORDS.items():
        for row in rows:
            text = "\n".join(str(row.get(field, "")) for field in [
                "原始来源标题",
                "来源内容",
                "选题标题",
                "选题命题",
                "一句话Brief",
                "我要做的实验",
                "我的工作流痛点",
                "重点体现",
            ])
            if any(keyword.lower() in text.lower() or keyword in text for keyword in keywords):
                fp = str(row.get("内容指纹") or row.get("原始来源标题") or row.get("选题标题"))
                if fp in seen:
                    continue
                seen.add(fp)
                samples.append({
                    "sample_key": sample_key,
                    "source_title": row.get("原始来源标题") or row.get("来源内容", ""),
                    "source_account": row.get("原始来源账号") or row.get("账号名/公众号名", ""),
                    "skill_decision": row.get("主编筛选") or row.get("主编判断", ""),
                    "editorial_trace": row.get("主编判断摘要", ""),
                    "title_thinking": row.get("标题思路", ""),
                    "topic": row.get("选题命题") or row.get("选题标题", ""),
                    "brief": row.get("一句话Brief", ""),
                    "experiment": row.get("我要做的实验", ""),
                    "pain": row.get("我的工作流痛点", ""),
                    "direction": row.get("对应方向", ""),
                    "action": row.get("推荐动作", ""),
                    "status": row.get("今日建议级别") or row.get("候选状态", ""),
                    "contract_status": row.get("field_contract_status", ""),
                    "contract_issues": row.get("field_contract_issues", ""),
                    "fallback_only": row.get("fallback_only", ""),
                })
                break
    return samples


def write_markdown_report(out_dir: Path, summary: dict[str, Any], samples: list[dict[str, Any]]) -> None:
    lines = [
        "# AR-020C Skill Thinking Replay Report",
        "",
        "本报告只读生产内容 CSV，不写飞书、不发 Topic Card、不触发 06。",
        "",
        "## Summary",
    ]
    for key in [
        "engine",
        "content_items",
        "candidate_count",
        "pre_skill_pool_count",
        "skill_rows",
        "actionable_count",
        "observe_count",
        "rejected_count",
        "contract_failure_count",
        "fallback_row_count",
        "title_quality_failure_count",
        "near_miss_count",
    ]:
        lines.append(f"- {key}: {summary.get(key)}")
    lines.extend(["", "## PM Sample Rows"])
    for row in samples:
        lines.extend([
            f"- {row['sample_key']} | {row['source_title'][:90]}",
            f"  - account: {row['source_account']}",
            f"  - status/action: {row['status']} / {row['action']}",
            f"  - direction: {row['direction']}",
            f"  - editorial trace: {row.get('editorial_trace', '')}",
            f"  - title thinking: {row.get('title_thinking', '')}",
            f"  - topic: {row['topic']}",
            f"  - experiment: {row['experiment']}",
            f"  - contract: {row['contract_status']} {row['contract_issues']}",
        ])
    lines.extend([
        "",
        "## Output Tables",
        f"- actionable: {summary.get('outputs', {}).get('skill_actionable')}",
        f"- observe: {summary.get('outputs', {}).get('skill_observe')}",
        f"- near_miss_high_fit_unselected: {summary.get('outputs', {}).get('near_miss_high_fit_unselected')}",
        f"- title_body_check: {summary.get('outputs', {}).get('title_body_check')}",
    ])
    (out_dir / "ar020c_user_sample_summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AR-020B real editorial Skill replay.")
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--content-csv", action="append", default=[], help="Specific content_items.csv path. Can be repeated.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--engine", choices=["codex", "deterministic"], default="codex")
    parser.add_argument("--codex-model", default="")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-skill-candidates", type=int, default=content_sampler.MAX_SKILL_REVIEW_CANDIDATES)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    since = date.fromisoformat(args.since)
    csv_paths = deterministic_replay.discover_content_csvs(args.content_csv)
    items = deterministic_replay.load_items(csv_paths, since)
    pre = build_pre_skill_pool(items, args.max_skill_candidates)
    write_csv(out_dir / "pre_skill_candidates.csv", pre["pre_skill_pool"])
    write_csv(out_dir / "candidate_universe.csv", pre["candidates"])

    skill_rows, engine_meta, engine = run_skill(pre["pre_skill_pool"], args)
    skill_rows = field_contract.apply_batch_quality_guards(skill_rows)
    classified = classify_rows(skill_rows)
    for name, rows in classified.items():
        write_csv(out_dir / f"skill_{name}.csv", rows)
    write_csv(out_dir / "skill_replay_rows.csv", skill_rows)

    reverse_rows = flow.reverse_evaluation_rows(
        skill_rows,
        pre["candidates"],
        pre["item_by_fp"],
        max_selected=args.max_skill_candidates,
    )
    flow.write_reverse_evaluation(out_dir / "skill_reverse_evaluation.csv", reverse_rows)
    write_csv(out_dir / "near_miss_high_fit_unselected.csv", near_miss_rows(reverse_rows))
    title_rows = title_body_check_rows(skill_rows)
    write_csv(out_dir / "title_body_check.csv", title_rows)

    samples = sample_rows(skill_rows)
    write_csv(out_dir / "skill_sample_table.csv", samples)
    summary = {
        "ok": True,
        "engine": engine,
        "engine_meta": engine_meta,
        "since": args.since,
        "input_files": [str(path) for path in csv_paths if path.exists()],
        "content_items": len(items),
        "candidate_count": len(pre["candidates"]),
        "pre_skill_pool_count": len(pre["pre_skill_pool"]),
        "skill_rows": len(skill_rows),
        "actionable_count": len(classified["actionable"]),
        "observe_count": len(classified["observe"]),
        "rejected_count": len(classified["rejected"]),
        "contract_failure_count": len(classified["contract_failures"]),
        "fallback_row_count": len(classified["fallback_rows"]),
        "reverse_flags": sum(1 for row in reverse_rows if row.potentially_better),
        "near_miss_count": len(near_miss_rows(reverse_rows)),
        "title_quality_failure_count": sum(1 for row in title_rows if row.get("title_quality_status") == "fail"),
        "title_quality_warning_count": sum(1 for row in title_rows if row.get("title_quality_status") == "warn"),
        "writes_feishu": False,
        "outputs": {
            "candidate_universe": str(out_dir / "candidate_universe.csv"),
            "pre_skill_candidates": str(out_dir / "pre_skill_candidates.csv"),
            "skill_replay_rows": str(out_dir / "skill_replay_rows.csv"),
            "skill_actionable": str(out_dir / "skill_actionable.csv"),
            "skill_observe": str(out_dir / "skill_observe.csv"),
            "skill_rejected": str(out_dir / "skill_rejected.csv"),
            "skill_contract_failures": str(out_dir / "skill_contract_failures.csv"),
            "skill_reverse_evaluation": str(out_dir / "skill_reverse_evaluation.csv"),
            "near_miss_high_fit_unselected": str(out_dir / "near_miss_high_fit_unselected.csv"),
            "title_body_check": str(out_dir / "title_body_check.csv"),
            "skill_sample_table": str(out_dir / "skill_sample_table.csv"),
            "user_sample_summary": str(out_dir / "ar020c_user_sample_summary.md"),
        },
    }
    write_markdown_report(out_dir, summary, samples)
    (out_dir / "skill_replay_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
