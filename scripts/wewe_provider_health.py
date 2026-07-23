#!/usr/bin/env python3
"""Read the exact run's direct WeWe refresh result."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def validate_result(path: Path, run_id: str, run_started_at_ms: int) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("refresh_result_unreadable") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("status") != "success":
        raise ValueError("refresh_result_not_success")
    if payload.get("run_id") != run_id or int(payload.get("run_started_at_ms") or 0) != run_started_at_ms:
        raise ValueError("refresh_result_run_mismatch")
    if int(payload.get("provider_request_count") or 0) != 1:
        raise ValueError("refresh_request_count_invalid")
    if not isinstance(payload.get("before"), dict) or not isinstance(payload.get("after"), dict):
        raise ValueError("refresh_result_snapshot_missing")
    return payload


def health_result(payload: dict[str, Any]) -> dict[str, Any]:
    after = payload["after"]
    return {
        "ok": True,
        "state": "updated_with_new_items" if int(payload.get("new_item_count") or 0) else "updated_no_new_items",
        "run_id": payload["run_id"],
        "provider_request_count": 1,
        "new_item_count": int(payload.get("new_item_count") or 0),
        "active_account_count": int(after.get("active_account_count") or 0),
        "active_source_count": len(after.get("feeds") or []),
        "article_count": sum(int(row.get("article_count") or 0) for row in after.get("feeds") or []),
        "database_readable": True,
        "secret_material_read": False,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-started-at-ms", type=int, required=True)
    parser.add_argument("--refresh-result", required=True)
    args = parser.parse_args()
    try:
        result = health_result(validate_result(Path(args.refresh_result), args.run_id, args.run_started_at_ms))
    except (OSError, ValueError) as exc:
        result = {"ok": False, "state": "provider_failed", "reason": str(exc), "run_id": args.run_id, "secret_material_read": False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
