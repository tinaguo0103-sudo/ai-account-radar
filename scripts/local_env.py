"""Load local, git-ignored environment variables for CLI scripts.

This project needs Feishu credentials for write operations. The credentials
must stay local and ignored by Git, so scripts load .env.local/.env when present
instead of asking the user to re-export variables in every new shell.
"""
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILES = (ROOT / ".env.local", ROOT / ".env")


def load_local_env() -> None:
    for env_file in ENV_FILES:
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
