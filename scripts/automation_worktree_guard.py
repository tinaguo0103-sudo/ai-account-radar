"""Guard scheduled automation from running in a development worktree."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PRODUCTION_DIR_NAME = "ai_account_radar"
DEFAULT_DEV_DIR_NAME = "ai_account_radar_dev"
DEFAULT_PRODUCTION_BRANCH = "main"


@dataclass(frozen=True)
class WorktreeGuardResult:
    ok: bool
    reason: str
    root: str
    branch: str
    expected_production_dir: str
    allowed_branches: list[str]


def truthy_env(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "y"}


def current_branch(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return "unknown"
    branch = result.stdout.strip()
    return branch or "unknown"


def configured_path(env_name: str, fallback: Path) -> Path:
    configured = str(os.getenv(env_name) or "").strip()
    return Path(configured).expanduser().resolve() if configured else fallback.resolve()


def allowed_production_branches() -> list[str]:
    raw = str(os.getenv("AI_ACCOUNT_RADAR_AUTOMATION_BRANCHES") or DEFAULT_PRODUCTION_BRANCH)
    branches = [part.strip() for part in raw.split(",") if part.strip()]
    return branches or [DEFAULT_PRODUCTION_BRANCH]


def check_automation_worktree(root: Path, *, allow_non_production: bool = False) -> WorktreeGuardResult:
    root = root.resolve()
    expected_production_dir = configured_path(
        "AI_ACCOUNT_RADAR_PRODUCTION_DIR",
        root.parent / DEFAULT_PRODUCTION_DIR_NAME,
    )
    expected_dev_dir = configured_path(
        "AI_ACCOUNT_RADAR_DEV_DIR",
        root.parent / DEFAULT_DEV_DIR_NAME,
    )
    branch = current_branch(root)
    allowed_branches = allowed_production_branches()

    if allow_non_production or truthy_env("AI_ACCOUNT_RADAR_ALLOW_NON_PRODUCTION_AUTOMATION"):
        return WorktreeGuardResult(
            ok=True,
            reason="override_allowed",
            root=str(root),
            branch=branch,
            expected_production_dir=str(expected_production_dir),
            allowed_branches=allowed_branches,
        )

    if root == expected_dev_dir or root.name.endswith("_dev"):
        return WorktreeGuardResult(
            ok=False,
            reason="running_from_development_worktree",
            root=str(root),
            branch=branch,
            expected_production_dir=str(expected_production_dir),
            allowed_branches=allowed_branches,
        )

    if expected_production_dir.exists() and root != expected_production_dir:
        return WorktreeGuardResult(
            ok=False,
            reason="running_from_unexpected_directory",
            root=str(root),
            branch=branch,
            expected_production_dir=str(expected_production_dir),
            allowed_branches=allowed_branches,
        )

    if branch not in allowed_branches:
        return WorktreeGuardResult(
            ok=False,
            reason="running_from_non_production_branch",
            root=str(root),
            branch=branch,
            expected_production_dir=str(expected_production_dir),
            allowed_branches=allowed_branches,
        )

    return WorktreeGuardResult(
        ok=True,
        reason="production_worktree_confirmed",
        root=str(root),
        branch=branch,
        expected_production_dir=str(expected_production_dir),
        allowed_branches=allowed_branches,
    )


def guard_failure_summary(result: WorktreeGuardResult, task_name: str) -> str:
    return (
        f"任务：{task_name}\n"
        f"结果：已阻止运行\n"
        f"原因：{result.reason}\n"
        f"当前目录：{result.root}\n"
        f"当前分支：{result.branch}\n"
        f"期望生产目录：{result.expected_production_dir}\n"
        f"允许生产分支：{', '.join(result.allowed_branches)}\n"
        "说明：这是自动化入口保护，避免 Codex 定时任务在开发 worktree 或功能分支上写入飞书。"
    )
