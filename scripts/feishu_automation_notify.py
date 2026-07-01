#!/usr/bin/env python3
"""Send lightweight Feishu notifications for scheduled automation exceptions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from typing import Any

import push_to_feishu as feishu
from local_env import load_local_env


def parse_receive_targets(raw_targets: list[str]) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for raw in raw_targets:
        for part in raw.split(","):
            text = part.strip()
            if not text:
                continue
            if ":" not in text:
                raise SystemExit(f"Invalid receive target: {text}. Expected type:id, e.g. chat_id:oc_xxx")
            target_type, target_id = text.split(":", 1)
            target_type = target_type.strip()
            target_id = target_id.strip()
            if not target_type or not target_id:
                raise SystemExit(f"Invalid receive target: {text}. Expected type:id, e.g. chat_id:oc_xxx")
            targets.append((target_type, target_id))
    return targets


def default_targets() -> list[tuple[str, str]]:
    raw = [
        os.getenv("FEISHU_AUTOMATION_NOTIFY_TARGETS", ""),
        os.getenv("FEISHU_CARD_RECEIVE_TARGETS", ""),
    ]
    targets = parse_receive_targets(raw)
    if not targets:
        raise SystemExit("Missing notify target. Set FEISHU_AUTOMATION_NOTIFY_TARGETS or FEISHU_CARD_RECEIVE_TARGETS.")
    return targets


def send_text(token: str, receive_id_type: str, receive_id: str, text: str, uuid_seed: str) -> dict[str, Any]:
    uuid = f"automation-notify-{hashlib.sha1(uuid_seed.encode('utf-8')).hexdigest()[:16]}"
    return feishu.request_json(
        "POST",
        f"/im/v1/messages?receive_id_type={receive_id_type}",
        token=token,
        body={
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
            "uuid": uuid,
        },
    )


def notify(title: str, body: str, targets: list[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    load_local_env()
    token = feishu.tenant_token()
    send_targets = targets or default_targets()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"{title}\n时间：{now}\n{body}".strip()
    responses: list[dict[str, Any]] = []
    for receive_id_type, receive_id in send_targets:
        responses.append(send_text(token, receive_id_type, receive_id, text, f"{title}|{body}|{receive_id_type}|{receive_id}|{now[:16]}"))
    return responses


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a Feishu automation notification.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--target", action="append", default=[], help="Optional explicit target in type:id form.")
    args = parser.parse_args()

    targets = parse_receive_targets(args.target) if args.target else None
    responses = notify(args.title, args.body, targets=targets)
    print(json.dumps({"ok": True, "sent": len(responses)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
