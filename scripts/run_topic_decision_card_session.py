#!/usr/bin/env python3
"""Send one Feishu topic decision card.

Daily production uses the cloud card receiver configured in Feishu Open
Platform, so this wrapper only sends the card. Card submissions are handled by
the cloud function and written back to Feishu 04.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
CARD_SCRIPT = ROOT / "scripts" / "feishu_topic_decision_card.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send one Feishu topic decision card. Cloud receiver handles callbacks.")
    parser.add_argument("--run-id", default="latest")
    parser.add_argument("--limit", type=int, default=7)
    parser.add_argument("--include-decided", action="store_true")
    parser.add_argument("--send-dry-run", action="store_true", help="Build the card and print send preview without sending.")
    parser.add_argument(
        "--receive-target",
        action="append",
        default=[],
        help="Receive target in type:id form. Can be repeated; otherwise FEISHU_CARD_RECEIVE_TARGETS is used.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_local_env()

    send_cmd = [
        sys.executable,
        str(CARD_SCRIPT),
        "send",
        "--run-id",
        args.run_id,
        "--limit",
        str(args.limit),
    ]
    if args.include_decided:
        send_cmd.append("--include-decided")
    if args.send_dry_run:
        send_cmd.append("--dry-run")
    for receive_target in args.receive_target:
        send_cmd.extend(["--receive-target", receive_target])

    print("[session] sending topic decision card; cloud receiver handles callbacks.", flush=True)
    result = subprocess.run(send_cmd, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    payload = {}
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("TOPIC_CARD_RESULT_JSON="):
            payload = json.loads(line.split("=", 1)[1])
            break
    session = {
        "ok": result.returncode == 0,
        "run_id": args.run_id,
        "record_count": int(payload.get("record_count") or 0),
        "sent_count": len(payload.get("send") or []) if isinstance(payload.get("send"), list) else 0,
        "reason": "already_sent_for_run" if result.returncode == 0 and int(payload.get("record_count") or 0) == 0 else "sent_or_previewed",
    }
    print("TOPIC_CARD_SESSION_RESULT_JSON=" + json.dumps(session, ensure_ascii=False, separators=(",", ":")))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
