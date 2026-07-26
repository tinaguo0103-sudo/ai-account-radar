#!/usr/bin/env python3
"""Loopback-only HTTP adapter over the source-control domain service."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from source_control import DEFAULT_DB, SourceControl, SourceControlError


class Handler(BaseHTTPRequestHandler):
    service: SourceControl

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
        try:
            if route.path == "/v1/sources":
                value = self.service.get_source_snapshot()
            elif route.path == "/v1/plan":
                value = self.service.build_collection_plan()
            elif route.path.startswith("/v1/commands/"):
                value = self.service.get_command_result(route.path.rsplit("/", 1)[-1])
            else:
                return self.send_json(404, {"error": "not_found"})
            self.send_json(200, value)
        except SourceControlError as error:
            self.send_json(409, {"error": str(error)})

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path)
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
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("source_control_http_must_bind_loopback")
    Handler.service = SourceControl(args.db)
    Handler.service.initialize()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
