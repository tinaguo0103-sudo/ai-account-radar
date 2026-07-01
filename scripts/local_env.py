"""Load local, git-ignored environment variables for CLI scripts.

This project needs Feishu credentials for write operations. The credentials
must stay local and ignored by Git, so scripts load .env.local/.env when present
instead of asking the user to re-export variables in every new shell.

Set AI_ACCOUNT_RADAR_ENV=staging to load .env.staging.local/.env.staging, or
AI_ACCOUNT_RADAR_ENV_FILE/ENV_FILE to load one explicit file. Explicit env files
do not fall back to .env.local, which avoids accidentally mixing staging and
production Feishu tokens.
"""
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILES = (ROOT / ".env.local", ROOT / ".env")


def env_files() -> tuple[Path, ...]:
    explicit = os.getenv("AI_ACCOUNT_RADAR_ENV_FILE") or os.getenv("ENV_FILE")
    if explicit:
        return (Path(explicit).expanduser(),)

    environment = (os.getenv("AI_ACCOUNT_RADAR_ENV") or "").strip().lower()
    if environment:
        if environment in {"prod", "production"}:
            return ENV_FILES
        safe_name = "".join(char for char in environment if char.isalnum() or char in {"-", "_"})
        if safe_name != environment:
            raise SystemExit(f"Invalid AI_ACCOUNT_RADAR_ENV: {environment}")
        return (ROOT / f".env.{safe_name}.local", ROOT / f".env.{safe_name}")

    return ENV_FILES


def load_local_env(*, required: bool = False) -> None:
    files = env_files()
    loaded = False
    for env_file in files:
        if not env_file.exists():
            continue
        loaded = True
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    if required and not loaded:
        raise SystemExit(f"No env file found. Checked: {', '.join(str(path) for path in files)}")
