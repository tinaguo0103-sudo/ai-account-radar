#!/usr/bin/env python3
"""Fail-closed account-plan validation for scheduled collection entrypoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


STATUS = "limited_plan_rejected"


@dataclass(frozen=True)
class AccountLimitGate:
    ok: bool
    value: int
    requested: str
    reason: str = ""


def validate_account_limit_argv(argv: Sequence[str], option: str = "--douyin-account-limit") -> AccountLimitGate:
    """Accept only an omitted option or the exact two-token form ``option 0``."""
    matches: list[tuple[int, str]] = []
    for index, token in enumerate(argv):
        if token == option:
            if index + 1 >= len(argv) or str(argv[index + 1]).startswith("--"):
                return AccountLimitGate(False, 0, "", "missing_account_limit_value")
            matches.append((index, str(argv[index + 1])))
        elif token.startswith(f"{option}="):
            return AccountLimitGate(False, 0, token.split("=", 1)[1], "account_limit_alias_rejected")

    if not matches:
        return AccountLimitGate(True, 0, "0")
    if len(matches) != 1:
        return AccountLimitGate(False, 0, ",".join(value for _, value in matches), "duplicate_account_limit")

    index, requested = matches[0]
    if index + 2 < len(argv) and argv[index + 2] == option:
        return AccountLimitGate(False, 0, requested, "duplicate_account_limit")
    if requested != "0":
        return AccountLimitGate(False, 0, requested, "full_account_collection_requires_exact_zero")
    return AccountLimitGate(True, 0, requested)


def rejection_payload(layer: str, gate: AccountLimitGate) -> dict[str, object]:
    return {
        "ok": False,
        "status": STATUS,
        "reason": gate.reason,
        "layer": layer,
        "requested_account_limit": gate.requested,
        "side_effects_started": False,
        "env_loaded": False,
        "writes_feishu": False,
        "cache_accessed": False,
        "chrome_contacted": False,
        "collection_started": False,
        "notification_sent": False,
    }
