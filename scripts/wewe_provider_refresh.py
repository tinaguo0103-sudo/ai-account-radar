#!/usr/bin/env python3
"""Request one refresh from the fixed local wewe-rss provider and persist its receipt."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

PROVIDER_URL = "http://127.0.0.1:4000"


def request_refresh(
    run_id: str, run_started_at_ms: int, *,
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> dict[str, Any]:
    started = clock_ms()
    attempt_id = f"wewe-refresh-{uuid.uuid4().hex}"
    return {
        "ok": False, "status": "provider_failed", "reason": "refresh_surface_unverifiable",
        "run_id": run_id, "attempt_id": attempt_id,
        "run_started_at_ms": run_started_at_ms, "started_at_ms": started, "completed_at_ms": clock_ms(),
        "provider": "fixed_local_wewe_rss", "provider_url": PROVIDER_URL,
        "detail": "The installed provider exposes only asynchronous GET ?update=true without a bound completion receipt; scheduled freshness therefore fails closed until a receipt-capable adapter is released.",
        "starts_browser": False, "starts_provider": False, "secrets_read": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-started-at-ms", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = request_refresh(args.run_id, args.run_started_at_ms)
    target = Path(args.out).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
