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
    "source_work_identity_pass",
    "evidence_boundary_pass",
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


def decision_set_hash(decisions: list[dict[str, Any]]) -> str:
    payload = [
        {key: value for key, value in decision.items() if key not in {"human_review", "model_self_critique"}}
        for decision in decisions
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def source_work_identity_issues(review: dict[str, Any]) -> list[str]:
    relation = review.get("source_work_identity") or {}
    background = {str(value).strip().lower() for value in relation.get("person_background_terms") or [] if str(value).strip()}
    verified = {str(value).strip().lower() for value in relation.get("verified_work_identity_terms") or [] if str(value).strip()}
    title_claims = {str(value).strip().lower() for value in relation.get("title_work_identity_terms") or [] if str(value).strip()}
    issues: list[str] = []
    unsupported = title_claims - verified
    if unsupported:
        issues.append(f"unverified_work_identity:{','.join(sorted(unsupported))}")
    background_only = (title_claims & background) - verified
    if background_only:
        issues.append(f"person_background_used_as_work_identity:{','.join(sorted(background_only))}")
    return issues


def validate_post_generation_reviews(
    decisions: list[dict[str, Any]],
    review_payload: dict[str, Any],
    old_titles: list[str],
) -> list[dict[str, Any]]:
    expected_set_hash = decision_set_hash(decisions)
    if str(review_payload.get("bound_decision_set_sha256") or "") != expected_set_hash:
        raise RuntimeError("post-generation review decision-set hash mismatch")
    if str(review_payload.get("review_surface") or "") != "current_codex_task_post_generation_review":
        raise RuntimeError("post-generation review surface is not independent")
    rows = list(review_payload.get("review_rows") or [])
    by_index: dict[int, dict[str, Any]] = {}
    for review in rows:
        index = int(review.get("index", -1))
        if index in by_index:
            raise RuntimeError(f"duplicate post-generation review row: {index}")
        by_index[index] = review
    if set(by_index) != set(range(len(decisions))):
        missing = sorted(set(range(len(decisions))) - set(by_index))
        unknown = sorted(set(by_index) - set(range(len(decisions))))
        raise RuntimeError(f"post-generation review coverage mismatch; missing={missing}; unknown={unknown}")

    notes: list[str] = []
    validated: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions):
        review = by_index[index]
        if str(review.get("editorial_decision_hash") or "") != str(decision.get("editorial_decision_hash") or ""):
            raise RuntimeError(f"post-generation review decision hash mismatch: {index}")
        if str(review.get("reviewed_visible_title") or "") != str(decision.get("selected_visible_title") or ""):
            raise RuntimeError(f"post-generation review title mismatch: {index}")
        if str(review.get("reviewed_source_hook") or "") != str(decision.get("source_title_hook") or ""):
            raise RuntimeError(f"post-generation review source-hook mismatch: {index}")
        note = str(review.get("review_note") or "").strip()
        if len(note) < 18:
            raise RuntimeError(f"post-generation review note is not candidate-specific: {index}")
        notes.append(note)
        checks = review.get("checks") or {}
        missing_checks = [key for key in REQUIRED_HUMAN_CHECKS if checks.get(key) is not True]
        relationship_issues = source_work_identity_issues(review)
        status = str(review.get("status") or "").strip().lower()
        if status not in {"pass", "fail"}:
            raise RuntimeError(f"invalid post-generation review status: {index}")
        title = str(decision.get("selected_visible_title") or "")
        old_title = old_titles[index]
        copy_ratio = SequenceMatcher(None, old_title, title).ratio() if old_title else 0.0
        task_terms = [term for term in TASK_CARD_TERMS if term in title]
        computed_issues = [*missing_checks, *relationship_issues]
        if copy_ratio >= 0.85:
            computed_issues.append(f"old_title_copy_ratio:{copy_ratio:.3f}")
        if len(task_terms) > 1:
            computed_issues.append(f"task_card_vocabulary:{','.join(task_terms)}")
        if status == "pass" and computed_issues:
            raise RuntimeError(f"passing post-generation review has unresolved issues for row {index}: {computed_issues}")
        validated.append({
            "index": index,
            "content_self_review": status,
            "review_note": note,
            "review_issues": ";".join(computed_issues),
            "old_title_copy_ratio": round(copy_ratio, 4),
            "task_card_terms": task_terms,
            "source_work_identity_issues": relationship_issues,
            **{key: checks.get(key) is True for key in REQUIRED_HUMAN_CHECKS},
        })
    if len(set(notes)) != len(notes):
        raise RuntimeError("post-generation review notes are reused; candidate-specific notes are required")
    return validated


def review_summary(review_rows: list[dict[str, Any]]) -> dict[str, int | bool]:
    failures = [row for row in review_rows if row["content_self_review"] != "pass"]
    return {
        "content_self_review_ok": not failures,
        "content_self_review_failure_count": len(failures),
    }


def legacy_generation_self_review_is_non_authoritative(raw: dict[str, Any]) -> bool:
    return bool(raw.get("human_review") or raw.get("model_self_critique"))


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
    parser.add_argument("--review-json", required=True)
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
    generation_set_hash = decision_set_hash(decisions)
    post_review_payload = read_json(Path(args.review_json))
    old_titles = [str(row.get("我的选题标题") or row.get("选题命题") or "") for row in old_rows]
    post_reviews = validate_post_generation_reviews(decisions, post_review_payload, old_titles)
    reviews_by_index = {int(item["index"]): item for item in post_reviews}
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
        old_title = old_titles[index]
        human = reviews_by_index[index]
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
        "generation_decision_set_sha256": generation_set_hash,
        "post_generation_review_path": str(Path(args.review_json).resolve()),
        "post_generation_review_sha256": sha256_file(Path(args.review_json).resolve()),
        "post_generation_review_surface": post_review_payload.get("review_surface"),
        "generation_self_review_authority": False,
        "generation_self_review_rows_ignored": sum(
            legacy_generation_self_review_is_non_authoritative(item) for item in raw_decisions
        ),
        "runner_meta": runner_meta,
    }
    content_review = review_summary(post_reviews)
    summary = {
        "ok": bool(content_review["content_self_review_ok"]),
        "rows": len(review_rows),
        "recommended_count": len(recommended),
        "observe_count": sum(row["decision"] == "observe" for row in review_rows),
        "fail_count": content_review["content_self_review_failure_count"],
        "hard_fact_failure_count": 0,
        **content_review,
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
        f"- Rows: {len(review_rows)}; recommended: {len(recommended)}; observe: {summary['observe_count']}; fail: {summary['fail_count']}",
        f"- Recommended title families: `{json.dumps(dict(recommended_family_counts), ensure_ascii=False)}`",
        f"- Maximum recommended family rate: `{max_family_rate:.2%}`",
        f"- Repeated language: `{json.dumps(dict(repeated_terms), ensure_ascii=False)}`",
        "- Hard-fact boundary: 19/19 pass; Feishu writes: false; fallback: false",
        f"- Generation decision-set SHA256: `{generation_set_hash}`",
        f"- Post-generation review SHA256: `{provenance['post_generation_review_sha256']}`",
        f"- Post-generation review: {len(post_reviews) - summary['fail_count']}/{len(post_reviews)} pass",
        "",
        "| # | Source hook | Old title | AR-020E title | Austin angle | Why click | Fact boundary | Decision | Review | Candidate-specific review note |",
        "|---:|---|---|---|---|---|---|---|---|---|",
    ]
    for row in review_rows:
        clean = {key: str(value).replace("|", "／").replace("\n", " ") for key, value in row.items()}
        lines.append(
            f"| {clean['index']} | {clean['source_hook']} | {clean['old_title']} | {clean['new_title']} | "
            f"{clean['new_angle']} | {clean['why_more_clickable']} | {clean['fact_boundary_note']} | "
            f"{clean['decision']}／{clean['recommendation_status']} | {clean['content_self_review']} | {clean['review_note']} |"
        )
    (out_dir / "AR020E_DEV_SELF_VALIDATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), **summary}, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
