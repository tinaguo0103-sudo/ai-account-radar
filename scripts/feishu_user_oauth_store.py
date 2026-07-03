#!/usr/bin/env python3
"""Keep Feishu user OAuth credentials in sync between repo and runtime copies."""
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_DIR = Path.home() / ".codex" / "ai-account-radar-runtime"
RUNTIME_SOURCE_FILE = "RUNTIME_SOURCE.txt"
SCRIPT_PACKAGE_TOKEN_KEYS = (
    "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN",
    "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN",
    "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN_EXPIRES_AT",
    "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN_EXPIRES_AT",
)


def env_quote(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def env_unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.replace('\\"', '"').replace("\\\\", "\\")


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = env_unquote(value)
    return values


def update_env_file(path: Path, values: dict[str, str]) -> None:
    clean_values = {key: str(value) for key, value in values.items() if value is not None}
    if not clean_values:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in clean_values:
            updated.append(f"{key}={env_quote(clean_values[key])}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in clean_values.items():
        if key not in seen:
            updated.append(f"{key}={env_quote(value)}")
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def runtime_source_root(root: Path = ROOT) -> Path | None:
    source_file = root / RUNTIME_SOURCE_FILE
    if not source_file.exists():
        return None
    for line in source_file.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Synced from:"):
            continue
        raw_path = line.split(":", 1)[1].strip()
        if raw_path:
            return Path(raw_path).expanduser().resolve()
    return None


def runtime_declares_source(runtime_dir: Path, source_root: Path) -> bool:
    declared = runtime_source_root(runtime_dir)
    return bool(declared and declared == source_root.resolve())


def related_env_files(root: Path = ROOT, runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> list[Path]:
    explicit = os.getenv("AI_ACCOUNT_RADAR_ENV_FILE") or os.getenv("ENV_FILE")
    if explicit:
        return [Path(explicit).expanduser().resolve()]

    root = root.expanduser().resolve()
    runtime_dir = runtime_dir.expanduser().resolve()

    source_root = runtime_source_root(root)
    if source_root and source_root != root:
        # When running from the runtime copy, write only the runtime env file.
        # LaunchAgent processes may not have Desktop/TCC permission to update
        # the source worktree, and OAuth refresh tokens are single-use.
        return [root / ".env.local"]

    roots: list[Path] = [root]

    if runtime_dir != root and runtime_dir.exists() and runtime_declares_source(runtime_dir, root):
        roots.append(runtime_dir)

    env_files: list[Path] = []
    seen: set[Path] = set()
    for item in roots:
        env_file = item / ".env.local"
        if env_file not in seen:
            env_files.append(env_file)
            seen.add(env_file)
    return env_files


def token_values_from_env_file(path: Path) -> dict[str, str]:
    values = read_env_file(path)
    return {key: values[key] for key in SCRIPT_PACKAGE_TOKEN_KEYS if values.get(key)}


def token_freshness(values: dict[str, str]) -> tuple[int, int, int]:
    def as_int(key: str) -> int:
        try:
            return int(float(values.get(key, "0") or "0"))
        except ValueError:
            return 0

    return (
        as_int("FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN_EXPIRES_AT"),
        as_int("FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN_EXPIRES_AT"),
        1 if values.get("FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN") else 0,
    )


def latest_token_values(env_files: list[Path]) -> dict[str, str]:
    latest: dict[str, str] = {}
    latest_score = (0, 0, 0)
    for env_file in env_files:
        values = token_values_from_env_file(env_file)
        if not values:
            continue
        score = token_freshness(values)
        if score >= latest_score:
            latest = values
            latest_score = score
    return latest


def sync_user_tokens(values: dict[str, str], *, root: Path = ROOT, runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> list[Path]:
    env_files = related_env_files(root, runtime_dir)
    token_values = {key: str(values[key]) for key in SCRIPT_PACKAGE_TOKEN_KEYS if values.get(key)}
    if not token_values:
        return []
    saved_to: list[Path] = []
    errors: list[str] = []
    for env_file in env_files:
        try:
            update_env_file(env_file, token_values)
            saved_to.append(env_file)
        except OSError as exc:
            errors.append(f"{env_file}: {exc}")
    if not saved_to and errors:
        raise PermissionError("Could not save Feishu user OAuth tokens: " + "; ".join(errors))
    return saved_to


def preserve_latest_user_tokens(*, root: Path = ROOT, runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> tuple[dict[str, str], list[Path]]:
    env_files = related_env_files(root, runtime_dir)
    values = latest_token_values(env_files)
    return values, env_files
