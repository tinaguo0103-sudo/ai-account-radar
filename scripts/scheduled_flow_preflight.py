#!/usr/bin/env python3
"""Classify scheduled-flow runtime readiness without performing business I/O."""
from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from local_env import env_files, load_local_env


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT_TABLE_KEYS = {
    "collection": ("FEISHU_SOURCE_TABLE_ID", "FEISHU_CONTENT_TABLE_ID"),
    "editorial": ("FEISHU_TOPIC_TABLE_ID",),
    "card": ("FEISHU_TOPIC_TABLE_ID",),
}
CORE_ENV_KEYS = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN")


def is_staging_environment(environ: dict[str, str]) -> bool:
    name = environ.get("AI_ACCOUNT_RADAR_ENV", "").strip().lower()
    env_file = (environ.get("AI_ACCOUNT_RADAR_ENV_FILE") or environ.get("ENV_FILE") or "").lower()
    return name in {"staging", "test"} or "staging" in env_file


def writable_path(path: Path) -> bool:
    candidate = path.expanduser().absolute()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)


def api_hostname(environ: dict[str, str]) -> str:
    raw = environ.get("FEISHU_API_BASE_URL", "https://open.feishu.cn")
    return str(urlparse(raw).hostname or "")


def evaluate_preflight(
    entrypoint: str,
    *,
    environ: dict[str, str] | None = None,
    core_paths: list[Path] | None = None,
    optional_telemetry_paths: list[Path] | None = None,
    check_network: bool = False,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    path_probe: Callable[[Path], bool] = writable_path,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    if entrypoint not in ENTRYPOINT_TABLE_KEYS:
        raise ValueError(f"unknown_entrypoint:{entrypoint}")

    core_paths = core_paths or [ROOT / "output"]
    optional_telemetry_paths = optional_telemetry_paths or [ROOT / "output" / "logs"]
    core_unwritable = [str(path) for path in core_paths if not path_probe(path)]
    optional_unwritable = [str(path) for path in optional_telemetry_paths if not path_probe(path)]
    missing_env = [key for key in CORE_ENV_KEYS if not env.get(key, "").strip()]
    if is_staging_environment(env):
        missing_env.extend(key for key in ENTRYPOINT_TABLE_KEYS[entrypoint] if not env.get(key, "").strip())
        if entrypoint == "card" and not (
            env.get("FEISHU_CARD_RECEIVE_TARGETS", "").strip()
            or env.get("FEISHU_CARD_RECEIVE_ID", "").strip()
        ):
            missing_env.append("FEISHU_CARD_RECEIVE_TARGETS")
    missing_env = sorted(set(missing_env))

    dns_ok: bool | None = None
    dns_error = ""
    host = api_hostname(env)
    if check_network and not missing_env:
        try:
            if not host:
                raise OSError("missing_api_hostname")
            resolver(host, 443, type=socket.SOCK_STREAM)
            dns_ok = True
        except OSError as exc:
            dns_ok = False
            dns_error = f"{exc.__class__.__name__}: {exc}"

    classifications: list[str] = []
    if core_unwritable:
        classifications.append("cwd_or_core_path_not_writable")
    if missing_env:
        classifications.append("environment_not_loaded")
    if check_network and dns_ok is False:
        classifications.extend(["dns_network_unavailable", "core_external_write_unavailable"])
    if optional_unwritable:
        classifications.append("optional_telemetry_unavailable")

    blocking = [value for value in classifications if value != "optional_telemetry_unavailable"]
    return {
        "ok": not blocking,
        "entrypoint": entrypoint,
        "blocking_reasons": blocking,
        "classifications": classifications,
        "environment_loaded": not missing_env,
        "missing_environment_keys": missing_env,
        "core_paths_writable": not core_unwritable,
        "core_unwritable_paths": core_unwritable,
        "optional_telemetry_available": not optional_unwritable,
        "optional_telemetry_unwritable_paths": optional_unwritable,
        "dns_checked": check_network,
        "dns_available": dns_ok,
        "dns_error": dns_error,
        "api_host": host,
        "business_writes": 0,
        "external_calls": 0,
    }


def require_scheduled_flow_preflight(entrypoint: str, *, check_network: bool) -> dict[str, Any]:
    result = evaluate_preflight(entrypoint, check_network=check_network)
    if not result["ok"]:
        raise RuntimeError("scheduled_flow_preflight_failed:" + ",".join(result["blocking_reasons"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only scheduled-flow runtime preflight.")
    parser.add_argument("--entrypoint", required=True, choices=sorted(ENTRYPOINT_TABLE_KEYS))
    parser.add_argument("--check-network", action="store_true", help="Resolve the configured Feishu API host without calling it.")
    args = parser.parse_args()
    load_local_env()
    result = evaluate_preflight(args.entrypoint, check_network=args.check_network)
    result["env_files_checked"] = [str(path) for path in env_files()]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
