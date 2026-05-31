#!/usr/bin/env python3
"""Grant a Feishu user admin permission on the generated Base."""
from __future__ import annotations

import argparse
import json

import push_to_feishu as p


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-token", required=True)
    parser.add_argument("--member-type", choices=["email", "openid", "userid", "unionid"], required=True)
    parser.add_argument("--member-id", required=True)
    parser.add_argument("--perm", default="full_access", choices=["view", "edit", "full_access"])
    args = parser.parse_args()

    token = p.tenant_token()
    payload = p.request_json(
        "POST",
        f"/drive/v1/permissions/{args.app_token}/members?type=bitable",
        token=token,
        body={
            "member_type": args.member_type,
            "member_id": args.member_id,
            "perm": args.perm,
        },
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
