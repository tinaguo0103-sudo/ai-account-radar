#!/usr/bin/env python3
"""Apply Website source commands through the sole source-control domain service."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


class BridgeError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BridgeError(f"{name.lower()}_missing")
    return value


def endpoint(name: str, *, loopback: bool) -> str:
    value = required_env(name).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ({"http"} if loopback else {"http", "https"}):
        raise BridgeError(f"{name.lower()}_invalid")
    if loopback and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise BridgeError("source_control_endpoint_not_loopback")
    return value


def request_json(
    method: str,
    url: str,
    *,
    bearer: str = "",
    sites_bypass_bearer: str = "",
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if sites_bypass_bearer:
        headers["OAI-Sites-Authorization"] = f"Bearer {sites_bypass_bearer}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            value = json.loads(error.read())
            code = str(value.get("error") or f"http_{error.code}")
        except Exception:
            code = (
                "sites_siwc_machine_auth_failed"
                if sites_bypass_bearer and error.code in {401, 403}
                else f"http_{error.code}"
            )
        raise BridgeError(code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise BridgeError("bridge_transport_unavailable") from None
    if not isinstance(value, dict):
        raise BridgeError("bridge_response_invalid")
    return value


def source_identity(source_url: str) -> dict[str, Any]:
    identity = request_json("GET", f"{source_url}/v1/identity")
    if (
        identity.get("instance_id") != required_env("SOURCE_CONTROL_INSTANCE_ID")
        or identity.get("database_identity") != required_env("SOURCE_CONTROL_DATABASE_ID")
        or identity.get("authority") != "source_control_sqlite"
    ):
        raise BridgeError("source_control_identity_mismatch")
    return identity


def reconcile_domain_result(source_url: str, command_id: str) -> dict[str, Any] | None:
    try:
        result = request_json("GET", f"{source_url}/v1/commands/{command_id}")
    except BridgeError as error:
        if str(error) == "command_not_found":
            return None
        raise
    return result


def reconcile_bridge_receipt(
    bridge_url: str,
    command_id: str,
    status: str,
    sites_bypass_bearer: str,
) -> dict[str, Any] | None:
    try:
        readback = request_json(
            "GET",
            f"{bridge_url}/api/source-commands/{command_id}",
            sites_bypass_bearer=sites_bypass_bearer,
        )
    except BridgeError as error:
        if str(error) == "command_not_found":
            return None
        raise
    receipt = readback.get("receipt")
    if not isinstance(receipt, dict):
        return None
    if receipt.get("status") != status:
        raise BridgeError("bridge_receipt_readback_mismatch")
    return readback


def run_once() -> dict[str, Any]:
    bridge_url = endpoint("SOURCE_BRIDGE_URL", loopback=False)
    source_url = endpoint("SOURCE_CONTROL_URL", loopback=True)
    bearer = required_env("SOURCE_BRIDGE_BEARER")
    sites_bypass_bearer = required_env("SOURCE_BRIDGE_SIWC_BYPASS_BEARER")
    source_identity(source_url)

    claimed = request_json(
        "POST",
        f"{bridge_url}/api/source-bridge/claim",
        bearer=bearer,
        sites_bypass_bearer=sites_bypass_bearer,
        payload={},
    )
    command = claimed.get("command")
    if command is None:
        return {
            "ok": True,
            "status": "idle",
            "claimed": 0,
            "domain_actions": 0,
            "receipt_writes": 0,
        }
    if not isinstance(command, dict):
        raise BridgeError("bridge_claim_invalid")

    command_id = str(command.get("command_id") or "")
    expected_revision = int(command.get("expected_revision"))
    rollback_revision = command.get("rollback_revision")
    if rollback_revision is None:
        domain_path = "/v1/commands"
        domain_payload = {
            "command_id": command_id,
            "expected_revision": expected_revision,
            "operations": list(command.get("operations") or []),
            "actor": "website_source_bridge",
        }
    else:
        domain_path = "/v1/rollback"
        domain_payload = {
            "command_id": command_id,
            "expected_revision": expected_revision,
            "target_revision": int(rollback_revision),
        }

    result = reconcile_domain_result(source_url, command_id)
    reconciled = result is not None
    if result is None:
        try:
            result = request_json("POST", f"{source_url}{domain_path}", payload=domain_payload)
        except BridgeError as error:
            result = reconcile_domain_result(source_url, command_id)
            reconciled = result is not None
            if result is None:
                if str(error) == "bridge_transport_unavailable":
                    raise BridgeError("source_command_status_unknown") from None
                snapshot = request_json("GET", f"{source_url}/v1/sources")
                result = {
                    "status": "failed",
                    "applied_revision": None,
                    "result_sha256": None,
                    "error_code": str(error),
                }

    snapshot = request_json("GET", f"{source_url}/v1/sources")
    status = str(result.get("status") or "")
    if status not in {"applied", "conflict", "failed"}:
        raise BridgeError("source_command_result_invalid")
    completion = {
        "command_id": command_id,
        "claim_token": str(command.get("claim_token") or ""),
        "status": status,
        "applied_revision": result.get("applied_revision"),
        "result_sha256": result.get("result_sha256"),
        "error_code": result.get("error_code"),
        "projection": snapshot,
    }
    try:
        readback = request_json(
            "POST",
            f"{bridge_url}/api/source-bridge/complete",
            bearer=bearer,
            sites_bypass_bearer=sites_bypass_bearer,
            payload=completion,
        )
    except BridgeError:
        readback = reconcile_bridge_receipt(
            bridge_url,
            command_id,
            status,
            sites_bypass_bearer,
        )
        if readback is None:
            raise BridgeError("bridge_receipt_status_unknown") from None
        receipt = readback.get("receipt")
        return {
            "ok": True,
            "status": status,
            "command_id": command_id,
            "claimed": 1,
            "domain_actions": 0 if reconciled else 1,
            "domain_reconciled": reconciled,
            "receipt_writes": 0,
            "receipt_reconciled": True,
            "projection_revision": snapshot.get("revision"),
        }

    receipt = readback.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("status") != status:
        raise BridgeError("bridge_receipt_readback_mismatch")
    return {
        "ok": True,
        "status": status,
        "command_id": command_id,
        "claimed": 1,
        "domain_actions": 0 if reconciled else 1,
        "domain_reconciled": reconciled,
        "receipt_writes": 1,
        "projection_revision": snapshot.get("revision"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.check_only:
            bridge_url = endpoint("SOURCE_BRIDGE_URL", loopback=False)
            source_url = endpoint("SOURCE_CONTROL_URL", loopback=True)
            sites_bypass_bearer = required_env("SOURCE_BRIDGE_SIWC_BYPASS_BEARER")
            source_identity(source_url)
            health = request_json(
                "GET",
                f"{bridge_url}/api/source-bridge/health",
                sites_bypass_bearer=sites_bypass_bearer,
            )
            print(json.dumps({
                "ok": True,
                "status": "ready",
                "machine_auth": "sites_siwc_bypass",
                "bridge_health_ok": bool(health.get("ok")),
                "credentials_logged": False,
                "business_actions": 0,
            }, sort_keys=True))
            return 0
        while True:
            print(json.dumps(run_once(), ensure_ascii=False, sort_keys=True))
            if not args.watch:
                return 0
            time.sleep(max(1, args.interval_seconds))
    except BridgeError as error:
        print(json.dumps({
            "ok": False,
            "status": str(error),
            "credentials_logged": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
