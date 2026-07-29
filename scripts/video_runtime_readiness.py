#!/usr/bin/env python3
"""Fail-closed startup binding for the deterministic video runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from douyin_video_understanding_producer import (
    ProducerError,
    RUNTIME_CONFIG_ENV,
    validate_runtime,
)


class RuntimeReadinessError(RuntimeError):
    pass


def _read_object(path: Path, prefix: str) -> dict[str, Any]:
    try:
        if not path.exists():
            raise RuntimeReadinessError(f"{prefix}_missing")
        if not path.is_file():
            raise RuntimeReadinessError(f"{prefix}_not_file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except RuntimeReadinessError:
        raise
    except json.JSONDecodeError as error:
        raise RuntimeReadinessError(f"{prefix}_invalid_json") from error
    except (OSError, UnicodeError) as error:
        raise RuntimeReadinessError(f"{prefix}_unreadable") from error
    if not isinstance(value, dict):
        raise RuntimeReadinessError(f"{prefix}_invalid_schema")
    return value


def check_runtime_readiness(explicit_path: str = "") -> dict[str, Any]:
    raw = str(explicit_path or os.environ.get(RUNTIME_CONFIG_ENV, "")).strip()
    if not raw:
        raise RuntimeReadinessError("video_runtime_config_missing")
    config_path = Path(raw).expanduser()
    config = _read_object(config_path, "video_runtime_config")
    policy_raw = str(config.get("policy_path") or "").strip()
    if not policy_raw:
        raise RuntimeReadinessError("video_runtime_config_missing_policy_path")
    policy_path = Path(policy_raw).expanduser()
    policy = _read_object(policy_path, "video_policy")
    for field in (
        "schema_version", "policy_id", "target_count_max",
        "maximum_duration_seconds", "selection_contract", "models",
    ):
        if field not in policy:
            raise RuntimeReadinessError(f"video_policy_missing_field:{field}")
    try:
        runtime = validate_runtime(config)
    except ProducerError as error:
        raise RuntimeReadinessError(str(error)) from error
    return {
        "config_path": str(config_path.absolute()),
        "policy_path": str(policy_path.absolute()),
        "runtime": runtime,
    }
