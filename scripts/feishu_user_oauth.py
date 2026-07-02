#!/usr/bin/env python3
"""Authorize the Feishu app as the current user and save local OAuth tokens.

This is a local-only setup helper. It opens a Feishu OAuth URL, receives the
authorization callback on localhost, exchanges the code for user tokens, and
stores them in `.env.local` so 06 document sync can create docs in the user's
visible Drive folder.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from local_env import load_local_env

import push_to_feishu as feishu


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV_FILE = ROOT / ".env.local"
DEFAULT_PORT = 8789
DEFAULT_SCOPES = [
    "docx:document",
    "docx:document:create",
    "docx:document.block:convert",
    "docs:document:import",
    "space:document:retrieve",
    "drive:drive",
    "space:folder:create",
    "offline_access",
]


def local_env_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def update_local_env(values: dict[str, str]) -> None:
    LOCAL_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = LOCAL_ENV_FILE.read_text(encoding="utf-8").splitlines() if LOCAL_ENV_FILE.exists() else []
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            updated.append(f"{key}={local_env_quote(values[key])}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in values.items():
        if key not in seen:
            updated.append(f"{key}={local_env_quote(value)}")
    LOCAL_ENV_FILE.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def public_token_summary(data: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in data.items():
        if "token" in key.lower():
            summary[key] = "set" if value else ""
        else:
            summary[key] = value
    return summary


def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise SystemExit("Missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    payload = feishu.request_json(
        "POST",
        "/authen/v2/oauth/token",
        body={
            "grant_type": "authorization_code",
            "client_id": app_id,
            "client_secret": app_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    return payload.get("data", payload)


class CallbackHandler(BaseHTTPRequestHandler):
    server: "OAuthServer"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler API.
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.server.callback = {
            "path": parsed.path,
            "code": query.get("code", [""])[0],
            "state": query.get("state", [""])[0],
            "error": query.get("error", [""])[0],
            "error_description": query.get("error_description", [""])[0],
        }
        ok = bool(self.server.callback["code"])
        body = (
            "Feishu user authorization received. You can return to Codex."
            if ok
            else "Feishu authorization did not return a code. You can return to Codex."
        )
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


class OAuthServer(HTTPServer):
    callback: dict[str, str] | None = None


def wait_for_callback(port: int, timeout_seconds: int) -> dict[str, str]:
    server = OAuthServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = 1
    started = time.time()
    try:
        while time.time() - started < timeout_seconds:
            server.handle_request()
            if server.callback:
                return server.callback
    finally:
        server.server_close()
    raise TimeoutError(f"Timed out waiting for Feishu OAuth callback on localhost:{port}")


def build_authorize_url(redirect_uri: str, scopes: list[str], state: str) -> str:
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    if not app_id:
        raise SystemExit("Missing FEISHU_APP_ID")
    query = urlencode({
        "app_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
    })
    return f"https://open.feishu.cn/open-apis/authen/v1/authorize?{query}"


def save_tokens(data: dict[str, Any], require_refresh_token: bool = True) -> dict[str, str]:
    access_token = str(data.get("access_token") or data.get("user_access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    expires_in = int(data.get("expires_in") or data.get("access_token_expires_in") or 0)
    refresh_expires_in = int(data.get("refresh_expires_in") or data.get("refresh_token_expires_in") or 0)
    if not access_token:
        raise RuntimeError(f"OAuth token response did not contain access_token: {public_token_summary(data)}")
    if require_refresh_token and not refresh_token:
        raise RuntimeError(
            "OAuth token response did not contain refresh_token. "
            "Open Feishu Developer Console and enable the offline_access permission "
            "(持续访问已授权的数据), then run this script again."
        )
    now = int(time.time())
    values = {
        "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN": access_token,
    }
    if refresh_token:
        values["FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN"] = refresh_token
    if expires_in:
        values["FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN_EXPIRES_AT"] = str(now + expires_in)
    if refresh_expires_in:
        values["FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN_EXPIRES_AT"] = str(now + refresh_expires_in)
    update_local_env(values)
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authorize Feishu user identity for 06 document sync.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--redirect-uri", default="", help="Must match the Feishu app redirect URL if configured explicitly.")
    parser.add_argument("--scope", action="append", default=[], help="OAuth scope. Can be repeated. Defaults to 06 doc sync scopes.")
    parser.add_argument("--allow-without-refresh-token", action="store_true", help="Save short-lived access token even if Feishu does not return refresh_token.")
    parser.add_argument("--print-url-only", action="store_true")
    parser.add_argument("--no-open", action="store_true", help="Print the URL and wait for callback without opening a browser.")
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()
    redirect_uri = args.redirect_uri.strip() or f"http://127.0.0.1:{args.port}/feishu/oauth/callback"
    scopes = args.scope or DEFAULT_SCOPES
    state = secrets.token_urlsafe(18)
    authorize_url = build_authorize_url(redirect_uri, scopes, state)
    print(json.dumps({
        "redirect_uri": redirect_uri,
        "scopes": scopes,
        "authorize_url": authorize_url,
        "note": "If Feishu reports redirect_uri mismatch, add this redirect_uri in Developer Console -> Security Settings.",
    }, ensure_ascii=False, indent=2), flush=True)
    if args.print_url_only:
        return 0
    if not args.no_open:
        webbrowser.open(authorize_url)
    callback = wait_for_callback(args.port, args.timeout_seconds)
    if callback.get("state") != state:
        raise RuntimeError("OAuth state mismatch; refusing to save tokens.")
    if callback.get("error"):
        raise RuntimeError(f"Feishu OAuth error: {callback.get('error')} {callback.get('error_description')}")
    data = exchange_code(callback["code"], redirect_uri)
    saved = save_tokens(data, require_refresh_token=not args.allow_without_refresh_token)
    print(json.dumps({
        "ok": True,
        "saved_to": str(LOCAL_ENV_FILE),
        "token_response": public_token_summary(data),
        "saved": public_token_summary(saved),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
