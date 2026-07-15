#!/usr/bin/env python3
"""Hook-first editorial expression policy with a narrow hard-fact boundary."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


POLICY_VERSION = "ar020e_hook_first_v1"
RHETORICAL_EXPRESSIONS = (
    "正在接管", "已经开始", "最值得", "没人意识到", "抢饭碗", "一个人顶一支团队",
)
EXACT_TOKEN_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*%"),
    re.compile(r"(?<![A-Za-z0-9])20\d{2}(?:[-/.年]\d{1,2})?(?:[-/.月]\d{1,2})?"),
    re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:FPS|fps|万元|亿元|美元|倍)"),
)
DIRECT_QUOTE_PATTERN = re.compile(r"[“\"]([^”\"]{4,80})[”\"]")
STATISTICAL_ASSERTION_PATTERN = re.compile(r"最常|大多数|占比|排名第|超过\s*\d|增长\s*\d|下降\s*\d")
OFFICIAL_ASSERTION_PATTERN = re.compile(r"官方(?:宣布|确认|表示|称|数据显示|发布)")
HIGH_RISK_FACT_PATTERN = re.compile(r"违法|合法|治愈|死亡率|保证收益|收益率|犯罪|诈骗|抄袭|造假")


class ExpressionPolicyError(RuntimeError):
    pass


def normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def evidence_corpus(dossier: dict[str, Any]) -> str:
    values: list[str] = []
    source = dossier.get("source") or {}
    values.extend(str(item.get("text") or "") for item in source.get("content_evidence") or [])
    for item in dossier.get("results") or []:
        values.extend([
            str(item.get("title") or ""),
            str(item.get("supporting_excerpt") or ""),
            str(item.get("supported_claim") or ""),
        ])
        raw_path = Path(str(item.get("dom_text_path") or ""))
        if raw_path.is_file():
            values.append(raw_path.read_text(encoding="utf-8"))
    return "\n".join(values)


def hard_fact_issues(text: str, dossier: dict[str, Any]) -> list[str]:
    corpus = evidence_corpus(dossier)
    normalized_corpus = normalize(corpus)
    issues: list[str] = []
    for pattern in EXACT_TOKEN_PATTERNS:
        for match in pattern.findall(str(text or "")):
            token = match if isinstance(match, str) else "".join(match)
            if normalize(token) not in normalized_corpus:
                issues.append(f"unsupported_exact_token:{token}")
    for quote in DIRECT_QUOTE_PATTERN.findall(str(text or "")):
        if normalize(quote) not in normalized_corpus:
            issues.append(f"unsupported_direct_quote:{quote}")
    if STATISTICAL_ASSERTION_PATTERN.search(str(text or "")):
        matched = STATISTICAL_ASSERTION_PATTERN.search(str(text or ""))
        phrase = matched.group(0) if matched else "statistical_assertion"
        if normalize(phrase) not in normalized_corpus:
            issues.append(f"unsupported_statistical_assertion:{phrase}")
    if OFFICIAL_ASSERTION_PATTERN.search(str(text or "")):
        matched = OFFICIAL_ASSERTION_PATTERN.search(str(text or ""))
        phrase = matched.group(0) if matched else "official_assertion"
        if normalize(phrase) not in normalized_corpus:
            issues.append(f"unsupported_official_assertion:{phrase}")
    if HIGH_RISK_FACT_PATTERN.search(str(text or "")):
        matched = HIGH_RISK_FACT_PATTERN.search(str(text or ""))
        phrase = matched.group(0) if matched else "high_risk_fact"
        if normalize(phrase) not in normalized_corpus:
            issues.append(f"unsupported_high_risk_fact:{phrase}")
    return sorted(set(issues))


def declared_hard_fact_required(text: str) -> bool:
    return bool(
        any(pattern.search(str(text or "")) for pattern in EXACT_TOKEN_PATTERNS)
        or DIRECT_QUOTE_PATTERN.search(str(text or ""))
        or STATISTICAL_ASSERTION_PATTERN.search(str(text or ""))
        or OFFICIAL_ASSERTION_PATTERN.search(str(text or ""))
        or HIGH_RISK_FACT_PATTERN.search(str(text or ""))
    )


def validate_editorial_decision(decision: dict[str, Any], dossier: dict[str, Any]) -> dict[str, Any]:
    if str(decision.get("editorial_expression_mode") or "") != "hook_first_aggressive_honest":
        raise ExpressionPolicyError("invalid_editorial_expression_mode")
    visible_text = "\n".join(str(decision.get(field) or "") for field in [
        "selected_visible_title", "natural_austin_angle", "public_decision_summary",
    ])
    issues = hard_fact_issues(visible_text, dossier)
    if issues:
        raise ExpressionPolicyError(";".join(issues))
    hard_fact_usage = str(decision.get("hard_fact_usage") or "").strip()
    if declared_hard_fact_required(visible_text) and hard_fact_usage.lower() in {"", "none", "无"}:
        raise ExpressionPolicyError("hard_fact_usage_not_declared")
    result = {
        "policy_version": POLICY_VERSION,
        "hard_fact_boundary_status": "pass",
        "hard_fact_usage": hard_fact_usage or "none",
        "fact_boundary_note": str(decision.get("fact_boundary_note") or "No unsupported verifiable hard fact."),
        "visible_text_sha256": hashlib.sha256(visible_text.encode("utf-8")).hexdigest(),
    }
    return result


def main() -> int:
    print(json.dumps({"policy_version": POLICY_VERSION, "rhetorical_expressions_allowed": RHETORICAL_EXPRESSIONS}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
