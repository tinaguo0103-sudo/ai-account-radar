#!/usr/bin/env python3
"""Validate paired AR-020D persona counterfactual outputs and leakage metrics."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

FACT_PATHS = ("source", "research", "hook_analysis")
ELIGIBILITY_FIELDS = ("decision", "recommendation_status")
EXPRESSION_FIELDS = ("natural_austin_angle", "selected_visible_title", "title_rationale", "public_decision_summary")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def compare_pair(with_persona: dict[str, Any], without_persona: dict[str, Any]) -> dict[str, Any]:
    facts_equal = all(stable_hash(with_persona.get(key)) == stable_hash(without_persona.get(key)) for key in FACT_PATHS)
    eligibility_equal = all(with_persona.get(key) == without_persona.get(key) for key in ELIGIBILITY_FIELDS)
    changed = [key for key in EXPRESSION_FIELDS if with_persona.get(key) != without_persona.get(key)]
    return {
        "candidate_id": with_persona.get("candidate_id"),
        "facts_stable": facts_equal,
        "eligibility_stable": eligibility_equal,
        "expression_fields_changed": changed,
        "persona_changes_expression_only": facts_equal and eligibility_equal and bool(changed),
    }


def sentence_family(value: str) -> str:
    text = re.sub(r"[A-Za-z0-9_+.-]+", "X", str(value or ""))
    text = re.sub(r"[\u4e00-\u9fff]{2,}", "词", text)
    return re.sub(r"\s+", "", text)[:80]


def editorial_title_family(value: str) -> str:
    text = str(value or "").replace(" ", "")
    if ("不是" in text and ("是" in text or "而是" in text)) or ("不缺" in text and "缺" in text):
        return "contrast_not_but"
    if "为什么" in text or text.endswith("吗") or "什么" in text:
        return "public_question"
    if any(token in text for token in ["出圈", "火了", "爆"]):
        return "story_social_proof"
    if any(token in text for token in ["之后", "以后", "最后"]):
        return "result_consequence"
    return "declarative_other"


def actionable_title_family_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actionable = [row for row in rows if row.get("今日建议级别") == "推荐制作"]
    families = Counter(editorial_title_family(row.get("选题命题") or row.get("selected_visible_title") or "") for row in actionable)
    maximum = max(families.values(), default=0)
    rate = maximum / len(actionable) if actionable else 0
    applicable = len(actionable) >= 4
    return {
        "actionable_count": len(actionable),
        "family_counts": dict(families),
        "max_family_rate": rate,
        "threshold": 0.30,
        "minimum_applicable_n": 4,
        "applicable": applicable,
        "classification": "evaluated" if applicable else "not_applicable_small_n",
        "ok": not applicable or rate <= 0.30,
    }


def leakage_report(rows: list[dict[str, Any]], retrievals: list[dict[str, Any]]) -> dict[str, Any]:
    sets = [tuple(item.get("example_ids") or []) for item in retrievals]
    families = Counter(sentence_family(row.get("selected_visible_title") or row.get("public_decision_summary") or "") for row in rows)
    nonempty = {key: count for key, count in families.items() if key}
    return {
        "candidate_count": len(rows),
        "unique_retrieval_sets": len(set(sets)),
        "all_candidates_same_retrieval": len(set(sets)) <= 1 if sets else True,
        "sentence_family_counts": nonempty,
        "max_sentence_family_rate": (max(nonempty.values()) / len(rows)) if rows and nonempty else 0,
        "persona_case_anchor_count": sum(
            1 for row in rows if row.get("case_id") or row.get("case_name") or row.get("case_citation")
        ),
        "actionable_title_families": actionable_title_family_report(rows),
    }


def write_report(out_dir: Path, pairs: list[dict[str, Any]], rows: list[dict[str, Any]], retrievals: list[dict[str, Any]]) -> dict[str, Any]:
    comparisons = [compare_pair(item["with_persona"], item["without_persona"]) for item in pairs]
    payload = {
        "comparisons": comparisons,
        "all_facts_stable": all(item["facts_stable"] for item in comparisons),
        "all_eligibility_stable": all(item["eligibility_stable"] for item in comparisons),
        "leakage": leakage_report(rows, retrievals),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "persona_counterfactual_audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
