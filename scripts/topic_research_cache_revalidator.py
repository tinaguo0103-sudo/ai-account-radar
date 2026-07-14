#!/usr/bin/env python3
"""Revalidate a recent research dossier against a freshly opened exact source."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from topic_research_contract import hash_json


TTL_SECONDS = {"news": 86400, "product_update": 259200, "evergreen": 604800}


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def revalidate(
    previous_source: dict[str, Any],
    current_source: dict[str, Any],
    cached: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    for field in ("exact_url", "exact_title", "author"):
        if str(previous_source.get(field) or "").strip() != str(current_source.get(field) or "").strip():
            raise ValueError(f"research cache exact-source identity changed: {field}")
    source_type = str(cached.get("source_type") or "evergreen")
    ttl = TTL_SECONDS.get(source_type, TTL_SECONDS["evergreen"])
    age = (now - _time(str(cached["completed_at"]))).total_seconds()
    if age < 0 or age > ttl:
        raise ValueError("research cache expired")
    if cached.get("status") != "completed" or not cached.get("queries"):
        raise ValueError("research cache is not a completed query-backed dossier")
    output = {key: value for key, value in cached.items() if key != "dossier_hash"}
    output.update({
        "source_content_hash": current_source["captured_content_hash"],
        "completed_at": now.isoformat(),
        "cache_revalidation": {
            "status": "revalidated",
            "previous_source_hash": previous_source["captured_content_hash"],
            "current_source_hash": current_source["captured_content_hash"],
            "identity_fields": ["exact_url", "exact_title", "author"],
            "cached_completed_at": cached["completed_at"],
            "ttl_seconds": ttl,
        },
    })
    output["dossier_hash"] = hash_json(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-source", required=True)
    parser.add_argument("--current-source", required=True)
    parser.add_argument("--cached-dossier", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    load = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(revalidate(load(args.previous_source), load(args.current_source), load(args.cached_dossier)), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
