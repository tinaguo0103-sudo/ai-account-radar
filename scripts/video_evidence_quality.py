"""Keep media access, recognition and semantic review as separate states."""
from __future__ import annotations

import copy
from typing import Any


def evidence_quality(package: dict[str, Any]) -> dict[str, Any]:
    stored = package.get("evidence_quality")
    if isinstance(stored, dict):
        return copy.deepcopy(stored)
    unresolved = package.get("unresolved_terms") or []
    failures = package.get("failures") or []
    ocr = package.get("ocr") if isinstance(package.get("ocr"), dict) else {}
    asr = package.get("asr") if isinstance(package.get("asr"), dict) else {}
    return {
        "media_access": "accessible" if ocr.get("media_sha256") or asr.get("text") else "not_verified",
        "ocr": "completed" if ocr.get("frame_count") else "not_verified",
        "asr": "completed" if asr.get("text") else "not_verified",
        "visual_understanding": "not_reviewed",
        "fact_confirmation": "pending" if unresolved or failures else "not_reviewed",
        "unresolved_terms": copy.deepcopy(unresolved),
        "failures": copy.deepcopy(failures),
        "fully_verified": False,
    }


def source_is_partial(package: dict[str, Any]) -> bool:
    """Recognition completion does not imply visual or fact-review completion."""
    quality = evidence_quality(package)
    pending = {"pending", "not_reviewed", "not_verified", "unavailable", "completed_empty"}
    return bool(
        package.get("status") in {"completed_with_failures", "media_read_complete_fact_review_pending"}
        or package.get("unresolved_terms")
        or package.get("failures")
        or quality.get("fact_confirmation") in pending
        or quality.get("visual_understanding") in pending
        or quality.get("asr") in {"not_verified", "completed_empty"}
    )


def unresolved_evidence(value: Any, source_url: str) -> dict[str, Any]:
    row = copy.deepcopy(value) if isinstance(value, dict) else {"term": str(value)}
    row.setdefault("source_url", source_url)
    return row
