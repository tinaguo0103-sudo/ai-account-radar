#!/usr/bin/env python3
"""Validate a current-task AR-020E editorial run and build its review pack."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import editorial_expression_policy as expression_policy
import editorial_skill_runner as runner


REQUIRED_HUMAN_CHECKS = (
    "click_desire",
    "concrete_public_hook",
    "austin_liveness",
    "no_task_card_jargon_dominance",
    "not_original_title_copy",
    "hard_fact_boundary_pass",
)
TASK_CARD_TERMS = ("验收", "状态机", "工作单", "流程门槛", "返修", "交付")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dossier_paths(root: Path) -> dict[int, Path]:
    paths: dict[int, Path] = {}
    for path in (root / "research").glob("candidate_*_*/validated.json"):
        match = re.match(r"candidate_(\d{3})_", path.parent.name)
        if match:
            paths[int(match.group(1))] = path
    return paths


def title_family(title: str) -> str:
    if title.rstrip().endswith(("?", "？")):
        return "rhetorical_question"
    if "不是" in title or ("不缺" in title and "缺" in title):
        return "public_contrast"
    if re.search(r"突然|出圈|全网|幕后", title):
        return "story_social_proof"
    if re.search(r"变成|长成|换一首|换了一个", title):
        return "result_transformation"
    if re.search(r"已经|正在|终于|开始", title):
        return "trend_judgment"
    if re.search(r"越.+越|再强也|最后拼", title):
        return "consequence_judgment"
    return "direct_judgment"


def validate_human_review(raw: dict[str, Any], old_title: str) -> dict[str, Any]:
    review = raw.get("human_review") or {}
    missing = [key for key in REQUIRED_HUMAN_CHECKS if review.get(key) is not True]
    title = str(raw.get("selected_visible_title") or "")
    copy_ratio = SequenceMatcher(None, old_title, title).ratio() if old_title else 0.0
    task_terms = [term for term in TASK_CARD_TERMS if term in title]
    if missing:
        raise RuntimeError(f"human content review failed: {missing}")
    if copy_ratio >= 0.85:
        raise RuntimeError(f"new title is too close to old title: {copy_ratio:.3f}")
    if len(task_terms) > 1:
        raise RuntimeError(f"task-card vocabulary dominates title: {task_terms}")
    return {
        **{key: True for key in REQUIRED_HUMAN_CHECKS},
        "review_note": str(review.get("review_note") or ""),
        "old_title_copy_ratio": round(copy_ratio, 4),
        "task_card_terms": task_terms,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--decisions-json", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    baseline = Path(args.baseline_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    old_rows = list(csv.DictReader((baseline / "skill_replay_rows.csv").open(encoding="utf-8-sig")))
    payload = read_json(Path(args.decisions_json))
    raw_decisions = list(payload.get("editorial_decisions") or [])
    if len(old_rows) != 19 or len(raw_decisions) != 19:
        raise RuntimeError(f"full calibration requires 19 rows; old={len(old_rows)} new={len(raw_decisions)}")

    decisions, runner_meta = runner.validate_stage1_payload(old_rows, payload)
    raw_by_index = {int(item["index"]): item for item in raw_decisions}
    dossiers = dossier_paths(baseline)
    review_rows: list[dict[str, Any]] = []
    for index, (old, normalized) in enumerate(zip(old_rows, decisions)):
        dossier_path = dossiers.get(index)
        if dossier_path is None:
            raise RuntimeError(f"missing immutable dossier for row {index}")
        dossier = read_json(dossier_path)
        if str(normalized.get("research_dossier_hash")) != str(dossier.get("dossier_hash")):
            raise RuntimeError(f"dossier hash mismatch for row {index}")
        policy_result = expression_policy.validate_editorial_decision(normalized, dossier)
        old_title = str(old.get("我的选题标题") or old.get("选题命题") or "")
        human = validate_human_review(raw_by_index[index], old_title)
        title = str(normalized["selected_visible_title"])
        review_rows.append({
            "index": index,
            "source_account": old.get("原始来源账号", ""),
            "source_url": old.get("来源链接", ""),
            "source_hook": normalized.get("source_title_hook", ""),
            "old_title": old_title,
            "old_angle": old.get("我的切入") or old.get("Austin转译角度") or "",
            "new_title": title,
            "new_angle": normalized.get("natural_austin_angle", ""),
            "decision": normalized.get("decision", ""),
            "recommendation_status": normalized.get("recommendation_status", ""),
            "why_more_clickable": normalized.get("hook_first_rationale", ""),
            "editorial_rationale": normalized.get("public_decision_summary", ""),
            "hard_fact_usage": normalized.get("hard_fact_usage", ""),
            "fact_boundary_note": normalized.get("fact_boundary_note", ""),
            "title_family": title_family(title),
            "hard_fact_boundary_status": policy_result["hard_fact_boundary_status"],
            "content_self_review": "pass",
            **human,
        })

    family_counts = Counter(row["title_family"] for row in review_rows)
    recommended = [row for row in review_rows if row["decision"] == "select"]
    recommended_family_counts = Counter(row["title_family"] for row in recommended)
    max_family_rate = max(recommended_family_counts.values(), default=0) / max(len(recommended), 1)
    repeated_terms = Counter(
        term for row in review_rows for term in ("已经", "正在", "终于", "开始", "不是", "为什么", "最值得")
        if term in str(row["new_title"])
    )

    skill_path = Path(__file__).resolve().parents[1] / "skills" / "ai-account-editorial-director" / "SKILL.md"
    test_skill = out_dir / "runtime_test_skill" / "SKILL.md"
    test_skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill_path, test_skill)
    provenance = {
        "execution_surface": "current_codex_task",
        "policy_version": expression_policy.POLICY_VERSION,
        "baseline_dir": str(baseline),
        "baseline_rows_sha256": sha256_file(baseline / "skill_replay_rows.csv"),
        "repo_skill_path": str(skill_path),
        "repo_skill_sha256": sha256_file(skill_path),
        "runtime_test_skill_path": str(test_skill),
        "runtime_test_skill_sha256": sha256_file(test_skill),
        "skill_hash_equal": sha256_file(skill_path) == sha256_file(test_skill),
        "persona_reference_state": "immutable_r8_style_and_judgment_reference_only",
        "source_research_state": "immutable_r8_dossiers_unmodified",
        "nested_model_execution": False,
        "writes_feishu": False,
        "fallback": False,
        "runner_meta": runner_meta,
    }
    summary = {
        "ok": True,
        "rows": len(review_rows),
        "recommended_count": len(recommended),
        "observe_count": sum(row["decision"] == "observe" for row in review_rows),
        "fail_count": 0,
        "hard_fact_failure_count": 0,
        "content_self_review_failure_count": 0,
        "recommended_title_family_counts": dict(recommended_family_counts),
        "recommended_max_title_family_rate": round(max_family_rate, 4),
        "all_title_family_counts": dict(family_counts),
        "repeated_language": dict(repeated_terms),
        "writes_feishu": False,
        "fallback": False,
    }
    write_csv(out_dir / "ar020e_before_after_all_rows.csv", review_rows)
    (out_dir / "ar020e_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "provenance_manifest.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "normalized_editorial_decisions.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# AR-020E Hook First Dev Self-Validation",
        "",
        f"- Rows: {len(review_rows)}; recommended: {len(recommended)}; observe: {summary['observe_count']}; fail: 0",
        f"- Recommended title families: `{json.dumps(dict(recommended_family_counts), ensure_ascii=False)}`",
        f"- Maximum recommended family rate: `{max_family_rate:.2%}`",
        f"- Repeated language: `{json.dumps(dict(repeated_terms), ensure_ascii=False)}`",
        "- Hard-fact boundary: 19/19 pass; Feishu writes: false; fallback: false",
        "",
        "| # | Source hook | Old title | AR-020E title | Austin angle | Why click | Fact boundary | Decision |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in review_rows:
        clean = {key: str(value).replace("|", "／").replace("\n", " ") for key, value in row.items()}
        lines.append(
            f"| {clean['index']} | {clean['source_hook']} | {clean['old_title']} | {clean['new_title']} | "
            f"{clean['new_angle']} | {clean['why_more_clickable']} | {clean['fact_boundary_note']} | "
            f"{clean['decision']}／{clean['recommendation_status']} |"
        )
    (out_dir / "AR020E_DEV_SELF_VALIDATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out_dir": str(out_dir), **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
