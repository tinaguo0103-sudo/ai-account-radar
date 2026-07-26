#!/usr/bin/env python3
"""Loopback-only HTTP adapter over the source-control domain service."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from source_control import DEFAULT_DB, SourceControl, SourceControlError

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PROFILE_IDENTITY = "fixed_douyin_profile_9333"


def run_preflight() -> dict:
    result = subprocess.run(
        ["python3", str(ROOT / "scripts" / "check_douyin_session.py"), "--port", "9333"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "status": "login_preflight_failed", "login_state": "indeterminate"}
    payload["returncode"] = result.returncode
    return payload


def open_fixed_profile_foreground() -> dict:
    result = subprocess.run(
        ["python3", str(ROOT / "scripts" / "start_douyin_cdp_chrome.py"), "--port", "9333", "--foreground"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    return {
        "ok": result.returncode == 0,
        "status": "fixed_profile_foreground_requested" if result.returncode == 0 else "fixed_profile_foreground_failed",
        "profile_identity": CANONICAL_PROFILE_IDENTITY,
    }

def start_exact_resume(run_id: str, database: Path) -> dict:
    run_root = ROOT / "output" / "runs" / run_id / "sources" / "douyin"
    if not run_root.is_dir():
        raise SourceControlError("douyin_resume_artifacts_missing")
    log_path = run_root / "manual_resume.log"
    handle = log_path.open("ab")
    try:
        process = subprocess.Popen(
            [
                "python3", str(ROOT / "scripts" / "resume_douyin_risk_run.py"),
                "--run-id", run_id,
                "--source-db", str(database),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        handle.close()
    return {
        "ok": True,
        "status": "resuming",
        "run_id": run_id,
        "profile_identity": CANONICAL_PROFILE_IDENTITY,
        "process_id": process.pid,
    }


class Handler(BaseHTTPRequestHandler):
    service: SourceControl
    instance_id: str
    request_counts: dict[str, int] = {}

    def count_request(self, path: str) -> None:
        self.request_counts[path] = self.request_counts.get(path, 0) + 1

    def send_json(self, status: int, value: object) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise SourceControlError("request_body_invalid")
        return value

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        self.count_request(route.path)
        try:
            if route.path == "/v1/sources":
                value = self.service.get_source_snapshot()
            elif route.path == "/v1/identity":
                value = self.service.get_authority_identity(self.instance_id)
            elif route.path == "/v1/plan":
                value = self.service.build_collection_plan()
            elif route.path == "/v1/douyin-risk":
                value = self.service.get_douyin_risk_state(parse_qs(route.query).get("run_id", [None])[0])
            elif route.path.startswith("/v1/commands/"):
                value = self.service.get_command_result(route.path.rsplit("/", 1)[-1])
            elif route.path == "/v1/request-counts":
                value = {"ok": True, "counts": dict(self.request_counts)}
            else:
                return self.send_json(404, {"error": "not_found"})
            self.send_json(200, value)
        except SourceControlError as error:
            self.send_json(409, {"error": str(error)})

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        self.count_request(route.path)
        try:
            payload = self.body()
            if route.path == "/v1/commands":
                value = self.service.apply_config_command(
                    str(payload.get("command_id") or ""),
                    int(payload.get("expected_revision")),
                    list(payload.get("operations") or []),
                    actor=str(payload.get("actor") or "local_ui"),
                )
            elif route.path == "/v1/rollback":
                value = self.service.rollback_to_revision(
                    str(payload.get("command_id") or ""),
                    int(payload.get("expected_revision")),
                    int(payload.get("target_revision")),
                )
            elif route.path == "/v1/douyin-risk/open-foreground":
                value = open_fixed_profile_foreground()
                if not value["ok"]:
                    return self.send_json(409, value)
            elif route.path == "/v1/douyin-risk/confirm-verification":
                run_id = str(payload.get("run_id") or "")
                current = self.service.get_douyin_risk_state(run_id)
                if current["state"]["profile_identity"] != CANONICAL_PROFILE_IDENTITY:
                    raise SourceControlError("douyin_profile_identity_mismatch")
                preflight = run_preflight()
                value = self.service.confirm_douyin_verification(
                    run_id,
                    CANONICAL_PROFILE_IDENTITY,
                    "session_verified" if preflight.get("ok") and preflight.get("login_state") == "logged_in"
                    else str(preflight.get("status") or "indeterminate"),
                )
                value["preflight"] = {
                    "ok": bool(preflight.get("ok")),
                    "status": str(preflight.get("status") or ""),
                    "login_state": str(preflight.get("login_state") or "indeterminate"),
                }
                if value["state"]["status"] == "resume_ready":
                    value["resume"] = start_exact_resume(run_id, self.service.path)
            else:
                return self.send_json(404, {"error": "not_found"})
            self.send_json(200, value)
        except (SourceControlError, ValueError, TypeError) as error:
            self.send_json(409, {"error": str(error)})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4180)
    parser.add_argument("--instance-id", required=True)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("source_control_http_must_bind_loopback")
    Handler.service = SourceControl(args.db)
    Handler.instance_id = args.instance_id
    Handler.request_counts = {}
    Handler.service.initialize()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
