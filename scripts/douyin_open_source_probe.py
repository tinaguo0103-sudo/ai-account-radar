#!/usr/bin/env python3
"""Dry-run probe for Douyin open-source ingestion routes.

This script does not write Feishu, does not download video/audio, and does not
read or store cookies. It compares the current built-in single-video resolver
with optional locally installed open-source tooling, then reports whether
homepage sampling needs a logged-in browser/source-watch probe.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "douyin_open_source_sources.example.yaml"
DEFAULT_OUT = ROOT / "output" / "spikes" / "douyin_open_source_tool_eval"
DEFAULT_RESOLVER = ROOT / "scripts" / "url_content_resolver.py"
LOCAL_DOUYIN_PY = ROOT / ".venv-douyin" / "bin" / "python"


def parse_scalar(value: str) -> Any:
    cleaned = value.strip().strip('"').strip("'")
    if cleaned.lower() == "true":
        return True
    if cleaned.lower() == "false":
        return False
    if cleaned.isdigit():
        return int(cleaned)
    return cleaned


def load_simple_yaml(path: Path) -> dict[str, list[dict[str, Any]]]:
    data: dict[str, list[dict[str, Any]]] = {}
    section = ""
    current: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if not raw_line.startswith(" ") and raw_line.strip().endswith(":"):
            if current is not None and section:
                data.setdefault(section, []).append(current)
            section = raw_line.strip()[:-1]
            current = None
            continue
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            if current is not None and section:
                data.setdefault(section, []).append(current)
            current = {}
            stripped = stripped[2:].strip()
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = parse_scalar(value)
            continue
        if current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = parse_scalar(value)
    if current is not None and section:
        data.setdefault(section, []).append(current)
    return data


def run(cmd: list[str], timeout: int = 30) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "cmd": cmd,
        }
    except Exception as exc:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "cmd": cmd}


def probe_builtin_resolver(url: str, out_dir: Path) -> dict[str, Any]:
    out_jsonl = out_dir / "builtin_url_resolver.jsonl"
    out_csv = out_dir / "builtin_url_resolver.csv"
    result = run([
        sys.executable,
        str(DEFAULT_RESOLVER),
        "--url",
        url,
        "--dry-run",
        "--out",
        str(out_jsonl),
        "--csv",
        str(out_csv),
        "--raw-dir",
        str(out_dir / "raw"),
    ])
    rows: list[dict[str, Any]] = []
    if out_jsonl.exists():
        rows = [json.loads(line) for line in out_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {
        "tool": "url_content_resolver._ROUTER_DATA",
        "input_type": "single_video_url",
        "status": "success" if result["ok"] and rows else "failed",
        "fields": sorted(rows[0].keys()) if rows else [],
        "items": rows,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def probe_douyin_mcp(url: str) -> dict[str, Any]:
    if not LOCAL_DOUYIN_PY.exists():
        return {
            "tool": "douyin-mcp-server / wanyi-watermark",
            "input_type": "single_video_url",
            "status": "not_installed",
            "failure_reason": ".venv-douyin/bin/python not found",
        }
    code = f"""
import json
from douyin_mcp_server.server import parse_douyin_link
url = {url!r}
try:
    print(parse_douyin_link(url))
except Exception as exc:
    print(json.dumps({{"status":"failed","error":type(exc).__name__ + ': ' + str(exc)}}, ensure_ascii=False))
"""
    with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8", delete=False) as handle:
        handle.write(code)
        temp_path = handle.name
    try:
        result = run([str(LOCAL_DOUYIN_PY), temp_path], timeout=30)
    finally:
        try:
            Path(temp_path).unlink()
        except FileNotFoundError:
            pass
    payload: Any = {}
    try:
        payload = json.loads(result["stdout"])
    except Exception:
        payload = {"raw_stdout": result["stdout"]}
    return {
        "tool": "douyin-mcp-server / wanyi-watermark",
        "input_type": "single_video_url",
        "status": "success" if result["ok"] and payload.get("status") == "success" else "failed",
        "payload": payload,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "requires_asr_key_for_transcript": True,
    }


def homepage_stub(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for account in accounts:
        rows.append({
            "tool": "MediaCrawler",
            "input_type": "douyin_homepage",
            "account_name": account.get("account_name", ""),
            "homepage_url": account.get("homepage_url", ""),
            "status": "needs_user_browser_state",
            "needs_login": True,
            "failure_reason": "主页最近N条需要用户用抖音小号在本机浏览器保持登录；本轮不读取或保存cookie/profile。",
            "recommended_next_step": "单独开 P1 source_watch_probe，低频测试 2-3 个账号，每账号最近3条。",
        })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run Douyin open-source tool capability probe.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--video-url", action="append", default=[])
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    cfg = load_simple_yaml(Path(args.config))
    urls = args.video_url or [row.get("url", "") for row in cfg.get("test_video_urls", []) if row.get("url")]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for url in urls:
        results.append(probe_builtin_resolver(url, out_dir))
        results.append(probe_douyin_mcp(url))
    results.extend(homepage_stub(cfg.get("accounts", [])))

    summary = {
        "ok": True,
        "video_urls": len(urls),
        "results": results,
        "notes": [
            "This probe never writes Feishu.",
            "This probe never downloads video/audio.",
            "Homepage sampling is intentionally marked needs_user_browser_state until a separate logged-in P1 probe is approved.",
        ],
    }
    (out_dir / "douyin_open_source_probe.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_dir / "douyin_open_source_probe.csv", results)
    print(json.dumps({
        "ok": True,
        "output": str(out_dir / "douyin_open_source_probe.json"),
        "csv": str(out_dir / "douyin_open_source_probe.csv"),
        "results": [
            {"tool": row.get("tool"), "input_type": row.get("input_type"), "status": row.get("status"), "account_name": row.get("account_name", "")}
            for row in results
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
