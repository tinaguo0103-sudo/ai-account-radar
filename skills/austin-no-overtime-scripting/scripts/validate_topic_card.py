#!/usr/bin/env python3
"""Validate an Austin scripting Topic Card."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from austin_scripting import load_records, normalize_topic, validate_topic


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to a Topic Card JSON file.")
    args = parser.parse_args()

    records = load_records(Path(args.input))
    results = []
    blocked = False
    for index, record in enumerate(records, 1):
        topic = normalize_topic(record, record_id=str(record.get("record_id") or f"sample-{index:03d}"))
        result = validate_topic(topic)
        blocked = blocked or result.status == "blocked"
        results.append({
            "ok": result.status != "blocked",
            "qa_status": result.status,
            "missing_required": result.missing_required,
            "evidence_gaps": result.evidence_gaps,
            "fact_check_points": result.fact_check_points,
            "notes": result.notes,
            "topic": topic,
        })
    print(json.dumps({
        "ok": not blocked,
        "cards": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2))
    return 0 if not blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
