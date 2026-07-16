#!/usr/bin/env python3
"""Start or check the local wewe-rss fulltext provider.

This helper keeps the production pipeline from silently skipping WeChat
fulltext because the local Docker service was not running. It does not read or
export cookies; login state stays inside the wewe-rss data volume.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path.home() / ".codex" / "ai-account-radar-runtime" / "providers" / "wewe-rss" / "data"
DEFAULT_CONTAINER_NAME = "ai-radar-wewe-rss"
DEFAULT_IMAGE = "cooderl/wewe-rss-sqlite:latest"
DEFAULT_BASE_URL = "http://127.0.0.1:4000"


def request_json(url: str, timeout: float = 3.0) -> dict | list | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ai-account-radar/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def service_ready(base_url: str) -> bool:
    url = f"{base_url.rstrip('/')}/feeds/all.json?limit=1&mode=fulltext"
    payload = request_json(url)
    return payload is not None


def run(command: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=check)


def docker_ready() -> bool:
    result = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return result.returncode == 0


def start_docker_desktop(wait_seconds: float) -> bool:
    if docker_ready():
        return True
    if sys.platform == "darwin":
        run(["open", "-a", "Docker"])
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if docker_ready():
            return True
        time.sleep(2)
    return docker_ready()


def container_exists(name: str) -> bool:
    result = run(["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
    return result.returncode == 0 and name in result.stdout.splitlines()


def container_running(name: str) -> bool:
    result = run(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
    return result.returncode == 0 and name in result.stdout.splitlines()


def start_container(name: str) -> subprocess.CompletedProcess:
    return run(["docker", "start", name])


def create_container(name: str, image: str, base_url: str, data_dir: Path, auth_code: str) -> subprocess.CompletedProcess:
    data_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(base_url)
    host_port = parsed.port or 4000
    return run([
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "-p",
        f"{host_port}:4000",
        "-e",
        "DATABASE_TYPE=sqlite",
        "-e",
        f"AUTH_CODE={auth_code}",
        "-e",
        "FEED_MODE=fulltext",
        "-e",
        f"SERVER_ORIGIN_URL={base_url.rstrip('/')}",
        "-v",
        f"{data_dir.expanduser().resolve()}:/app/data",
        image,
    ])


def wait_ready(base_url: str, wait_seconds: float) -> bool:
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if service_ready(base_url):
            return True
        time.sleep(2)
    return service_ready(base_url)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start/check local wewe-rss fulltext provider.")
    parser.add_argument("--base-url", default=os.getenv("WEWE_RSS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--container-name", default=os.getenv("WEWE_RSS_CONTAINER_NAME", DEFAULT_CONTAINER_NAME))
    parser.add_argument("--image", default=os.getenv("WEWE_RSS_IMAGE", DEFAULT_IMAGE))
    parser.add_argument("--data-dir", default=os.getenv("WEWE_RSS_DATA_DIR", str(DEFAULT_DATA_DIR)))
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--no-create", action="store_true", help="Only start an existing container; do not create a new one.")
    args = parser.parse_args()

    if service_ready(args.base_url):
        print(json.dumps({"ok": True, "status": "already_ready", "base_url": args.base_url}, ensure_ascii=False))
        return 0

    if args.check_only:
        print(json.dumps({"ok": False, "status": "not_ready", "base_url": args.base_url}, ensure_ascii=False))
        return 1

    if not start_docker_desktop(args.wait_seconds):
        print(json.dumps({"ok": False, "status": "docker_not_ready"}, ensure_ascii=False), file=sys.stderr)
        return 1

    if container_exists(args.container_name):
        if not container_running(args.container_name):
            result = start_container(args.container_name)
            if result.returncode != 0:
                print(result.stderr or result.stdout, file=sys.stderr)
                return result.returncode
    else:
        if args.no_create:
            print(json.dumps({"ok": False, "status": "container_missing", "container": args.container_name}, ensure_ascii=False), file=sys.stderr)
            return 1
        auth_code = os.getenv("WEWE_RSS_AUTH_CODE") or os.getenv("AI_RADAR_WEWE_RSS_AUTH_CODE")
        if not auth_code:
            print(
                json.dumps({
                    "ok": False,
                    "status": "missing_auth_code",
                    "note": "Set WEWE_RSS_AUTH_CODE in .env.local before creating a fresh wewe-rss container.",
                }, ensure_ascii=False),
                file=sys.stderr,
            )
            return 1
        result = create_container(args.container_name, args.image, args.base_url, Path(args.data_dir), auth_code)
        if result.returncode != 0:
            print(result.stderr or result.stdout, file=sys.stderr)
            return result.returncode

    if not wait_ready(args.base_url, args.wait_seconds):
        print(json.dumps({"ok": False, "status": "service_not_ready", "base_url": args.base_url}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "status": "ready", "base_url": args.base_url, "container": args.container_name}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
