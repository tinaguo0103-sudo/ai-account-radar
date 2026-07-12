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
