#!/usr/bin/env python3
"""Install/remove a macOS LaunchAgent for the 06 script package watcher."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from codex_cli_path import codex_runtime_diagnostics, resolve_codex_cli
from feishu_user_oauth_store import preserve_latest_user_tokens, sync_user_tokens


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_DIR = Path.home() / ".codex" / "ai-account-radar-runtime"
LABEL = "com.austin.ai-account-radar.script-package-watcher"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "ai-account-radar"
RUNTIME_DIRS = ("scripts", "config", "skills", "docs")
RUNTIME_FILES = ("README.md", ".env.local", ".env")
DEFAULT_DISPLAY_LINK_NAME = "06 完整脚本与制作包"


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def launchctl_target() -> str:
    return f"gui/{os.getuid()}"


def service_name() -> str:
    return f"{launchctl_target()}/{LABEL}"


def bootout() -> None:
    if not PLIST_PATH.exists():
        return
    run(["launchctl", "bootout", launchctl_target(), str(PLIST_PATH)], check=False)


def bootstrap() -> None:
    run(["launchctl", "bootstrap", launchctl_target(), str(PLIST_PATH)])
    run(["launchctl", "enable", service_name()], check=False)


def kickstart() -> None:
    run(["launchctl", "kickstart", "-k", service_name()], check=False)


def sync_runtime(runtime_dir: Path) -> None:
    latest_tokens, _ = preserve_latest_user_tokens(root=ROOT, runtime_dir=runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for dirname in RUNTIME_DIRS:
        source = ROOT / dirname
        target = runtime_dir / dirname
        if target.exists():
            shutil.rmtree(target)
        if source.exists():
            shutil.copytree(source, target)
    for filename in RUNTIME_FILES:
        source_file = ROOT / filename
        if source_file.exists():
            shutil.copy2(source_file, runtime_dir / filename)
    (runtime_dir / "RUNTIME_SOURCE.txt").write_text(
        f"Synced from: {ROOT}\n"
        "This runtime copy is used by the macOS LaunchAgent to avoid Desktop TCC restrictions.\n",
        encoding="utf-8",
    )
    if latest_tokens:
        sync_user_tokens(latest_tokens, root=ROOT, runtime_dir=runtime_dir)


def create_display_link(runtime_dir: Path, display_root: Path) -> None:
    target = runtime_dir / "output" / "script_execution_packages"
    target.mkdir(parents=True, exist_ok=True)
    display_root.parent.mkdir(parents=True, exist_ok=True)
    if display_root.is_symlink():
        if display_root.resolve() != target.resolve():
            display_root.unlink()
            display_root.symlink_to(target, target_is_directory=True)
        return
    if display_root.exists():
        raise SystemExit(
            f"Display path exists and is not a symlink: {display_root}\n"
            "Please rename it or choose --display-link-name."
        )
    display_root.symlink_to(target, target_is_directory=True)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_runtime_files() -> list[Path]:
    files: list[Path] = []
    for dirname in RUNTIME_DIRS:
        source = ROOT / dirname
        if source.exists():
            files.extend(path.relative_to(ROOT) for path in source.rglob("*") if path.is_file())
    for filename in RUNTIME_FILES:
        if (ROOT / filename).exists():
            files.append(Path(filename))
    return sorted(files)


def runtime_sync_report(runtime_dir: Path) -> dict[str, object]:
    missing: list[str] = []
    changed: list[str] = []
    checked = 0
    for rel_path in relative_runtime_files():
        source = ROOT / rel_path
        target = runtime_dir / rel_path
        checked += 1
        if not target.exists():
            missing.append(str(rel_path))
            continue
        if file_hash(source) != file_hash(target):
            changed.append(str(rel_path))
    return {
        "runtime": str(runtime_dir),
        "checked": checked,
        "missing": missing[:50],
        "changed": changed[:50],
        "missing_count": len(missing),
        "changed_count": len(changed),
        "in_sync": not missing and not changed,
    }


def build_plist(runtime_dir: Path, display_root: Path, interval_minutes: float, limit: int, max_age_days: int, python_bin: str) -> dict[str, object]:
    interval = max(1.0, float(interval_minutes))
    codex_bin = resolve_codex_cli(os.getenv("CODEX_BIN", ""))
    home = Path.home()
    codex_home = home / ".codex"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            python_bin,
            str(runtime_dir / "scripts" / "watch_script_package_queue.py"),
            "--interval-minutes",
            str(interval),
            "--limit",
            str(max(1, int(limit))),
            "--max-age-days",
            str(max(1, int(max_age_days))),
        ],
        "WorkingDirectory": str(runtime_dir),
        "EnvironmentVariables": {
            "PATH": f"{Path(codex_bin).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "CODEX_BIN": codex_bin,
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "PYTHONUNBUFFERED": "1",
            "SCRIPT_PACKAGE_OUTPUT_ROOT": str(runtime_dir / "output" / "script_execution_packages"),
            "SCRIPT_PACKAGE_DISPLAY_OUTPUT_ROOT": str(display_root),
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(LOG_DIR / "script_package_watcher_launch_agent.out.log"),
        "StandardErrorPath": str(LOG_DIR / "script_package_watcher_launch_agent.err.log"),
    }


def install(args: argparse.Namespace) -> None:
    python_bin = args.python_bin or sys.executable
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    project_doc_root = Path(args.project_doc_root).expanduser().resolve()
    display_root = (
        runtime_dir / "output" / "script_execution_packages"
        if args.no_display_link
        else project_doc_root / args.display_link_name
    )
    plist = build_plist(runtime_dir, display_root, args.interval_minutes, args.limit, args.max_age_days, python_bin)
    runtime_env = plist["EnvironmentVariables"]
    diagnostics = codex_runtime_diagnostics(
        str(runtime_env["CODEX_BIN"]),
        env={str(key): str(value) for key, value in runtime_env.items()},
    )
    if args.dry_run:
        print(json.dumps({"ok": diagnostics["ok"], "plist": plist, "codex_runtime": diagnostics}, ensure_ascii=False, indent=2))
        return
    if not diagnostics["ok"]:
        raise SystemExit("codex_runtime_unavailable: " + ",".join(diagnostics["reasons"]))
    if not args.no_sync_runtime:
        sync_runtime(runtime_dir)
    if not args.no_display_link:
        create_display_link(runtime_dir, display_root)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    Path(str(plist["StandardOutPath"])).write_text("", encoding="utf-8")
    Path(str(plist["StandardErrorPath"])).write_text("", encoding="utf-8")
    bootout()
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(plist, handle)
    bootstrap()
    if not args.no_kickstart:
        kickstart()
    print(f"installed {LABEL}")
    print(f"plist: {PLIST_PATH}")
    print(f"runtime: {runtime_dir}")
    print(f"display: {display_root}")
    print(f"stdout: {plist['StandardOutPath']}")
    print(f"stderr: {plist['StandardErrorPath']}")


def uninstall(dry_run: bool) -> None:
    if dry_run:
        print(f"would uninstall {LABEL} at {PLIST_PATH}")
        return
    bootout()
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print(f"uninstalled {LABEL}")


def status(runtime_dir: Path | None = None) -> int:
    result = run(["launchctl", "print", service_name()], check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if runtime_dir:
        print("runtime sync:")
        print(runtime_sync_report(runtime_dir))
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the 06 script package watcher as a user LaunchAgent.")
    parser.add_argument("--interval-minutes", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--max-age-days", type=int, default=5)
    parser.add_argument("--python-bin", default="", help="Python executable. Defaults to the interpreter running this installer.")
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR), help="Non-Desktop runtime directory used by LaunchAgent.")
    parser.add_argument("--project-doc-root", default=str(ROOT.parent), help="Human-facing project document root.")
    parser.add_argument("--display-link-name", default=DEFAULT_DISPLAY_LINK_NAME, help="Symlink name under --project-doc-root.")
    parser.add_argument("--no-display-link", action="store_true", help="Do not create a project-root symlink for generated packages.")
    parser.add_argument("--no-sync-runtime", action="store_true", help="Install plist without refreshing the runtime copy.")
    parser.add_argument("--sync-runtime-only", action="store_true", help="Refresh runtime copy and exit without touching LaunchAgent.")
    parser.add_argument("--no-kickstart", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.status:
        return status(Path(args.runtime_dir).expanduser().resolve())
    if args.sync_runtime_only:
        if args.dry_run:
            print(f"would sync runtime to {Path(args.runtime_dir).expanduser().resolve()}")
            return 0
        runtime_dir = Path(args.runtime_dir).expanduser().resolve()
        sync_runtime(runtime_dir)
        if not args.no_display_link:
            display_root = Path(args.project_doc_root).expanduser().resolve() / args.display_link_name
            create_display_link(runtime_dir, display_root)
            print(f"display link: {display_root}")
        print(f"synced runtime to {runtime_dir}")
        return 0
    if args.uninstall:
        uninstall(args.dry_run)
    else:
        install(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
