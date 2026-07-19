#!/usr/bin/env python3
"""Read back the fixed WeWe provider image/container refresh-owner contract."""
from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any, Callable


IMAGE = "ai-account-radar/wewe-rss-sqlite:2.6.1-ar039-no-cron"
UPSTREAM_COMMIT = "f88b023961804b986f3f1225c52d5066928df3c1"
REQUIRED_LABELS = {
    "ai-account-radar.wewe-refresh-owner": "project-signed-adapter-only",
    "ai-account-radar.wewe-internal-cron": "disabled-at-build",
    "ai-account-radar.wewe-upstream-commit": UPSTREAM_COMMIT,
}


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True)


def _inspect(kind: str, identity: str, runner: Callable[[list[str]], Any]) -> dict[str, Any]:
    result = runner(["docker", kind, "inspect", identity])
    if result.returncode != 0:
        return {"ok": False, "status": f"wewe_{kind}_contract_unavailable", "reason": (result.stderr or result.stdout).strip()}
    try:
        rows = json.loads(result.stdout)
        payload = rows[0]
        config = payload["Config"]
        labels = config.get("Labels") or {}
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        return {"ok": False, "status": f"wewe_{kind}_contract_malformed"}
    mismatches = {key: {"expected": value, "actual": labels.get(key)} for key, value in REQUIRED_LABELS.items() if labels.get(key) != value}
    if mismatches:
        return {"ok": False, "status": "internal_scheduler_not_disabled", "label_mismatches": mismatches}
    if any(str(value).startswith("CRON_EXPRESSION=") for value in config.get("Env") or []):
        return {"ok": False, "status": "internal_scheduler_configuration_present"}
    return {
        "ok": True,
        "status": "project_signed_adapter_is_refresh_owner",
        "identity": identity,
        "image": config.get("Image", identity) if kind == "container" else identity,
        "internal_scheduler": "disabled_at_build",
        "upstream_commit": UPSTREAM_COMMIT,
    }


def verify_image(image: str = IMAGE, runner: Callable[[list[str]], Any] = _run) -> dict[str, Any]:
    if image != IMAGE:
        return {"ok": False, "status": "unsupported_wewe_provider_image", "expected_image": IMAGE, "actual_image": image}
    return _inspect("image", image, runner)


def verify_container(name: str, runner: Callable[[list[str]], Any] = _run) -> dict[str, Any]:
    result = _inspect("container", name, runner)
    if result.get("ok") and result.get("image") != IMAGE:
        return {"ok": False, "status": "unsupported_wewe_provider_image", "expected_image": IMAGE, "actual_image": result.get("image")}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the project-owned WeWe refresh-owner runtime contract.")
    parser.add_argument("--container", default="ai-radar-wewe-rss")
    parser.add_argument("--image", default=IMAGE)
    parser.add_argument("--image-only", action="store_true")
    args = parser.parse_args()
    result = verify_image(args.image) if args.image_only else verify_container(args.container)
    result.update({"starts_provider": False, "refresh_requested": False, "writes_feishu": False})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
