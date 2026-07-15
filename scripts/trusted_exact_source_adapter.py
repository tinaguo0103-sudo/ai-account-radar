#!/usr/bin/env python3
"""Primary exact-source adapter routing and trusted-browser evidence checks."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


VERSION = "ar020d_primary_exact_source_adapter_v1"
DOUYIN_ADAPTER = "douyin_cdp_exact_video_v1"
TRUSTED_BROWSER_ADAPTER = "current_task_trusted_browser_exact_page_v1"
TRUSTED_WEB_ADAPTER = "trusted_web_exact_article_v1"


class AdapterContractError(RuntimeError):
    pass


def primary_adapter_for_url(url: str) -> str:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    if host == "douyin.com" or host.endswith(".douyin.com"):
        return DOUYIN_ADAPTER
    if host in {"x.com", "www.x.com", "claude.com", "www.claude.com"}:
        return TRUSTED_BROWSER_ADAPTER
    return TRUSTED_WEB_ADAPTER


def expected_identity(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in {"x.com", "www.x.com"}:
        match = re.fullmatch(r"/([^/]+)/status/(\d+)/?", parts.path)
        if not match:
            raise AdapterContractError("X exact source must be a concrete status URL")
        return {"kind": "x_status", "author_handle": match.group(1), "status_id": match.group(2)}
    if host in {"claude.com", "www.claude.com"}:
        if parts.path.rstrip("/") != "/blog/how-people-are-using-claude-cowork":
            raise AdapterContractError("Claude exact source path is not the approved article")
        return {"kind": "claude_blog", "path": "/blog/how-people-are-using-claude-cowork"}
    return {"kind": "concrete_url", "path": parts.path.rstrip("/") or "/"}


def validate_primary_adapter(candidate: dict[str, Any], output: dict[str, Any]) -> None:
    expected_adapter = str(candidate.get("primary_adapter") or "")
    if not expected_adapter:
        raise AdapterContractError("Candidate has no primary exact-source adapter")
    if output.get("primary_adapter") != expected_adapter:
        raise AdapterContractError("Source evidence came from a non-primary adapter")
    attempts = list(output.get("attempted_adapters") or [])
    if attempts != [expected_adapter]:
        raise AdapterContractError("Exactly one primary adapter may be attempted; failover is forbidden")
    if output.get("open_status") in {"failed", "source_open_failed"}:
        return
    expected = expected_identity(str(candidate.get("exact_url") or ""))
    identity = output.get("page_identity") or {}
    if identity.get("kind") != expected["kind"]:
        raise AdapterContractError("Exact-page identity kind mismatch")
    if expected["kind"] == "x_status":
        if identity.get("status_id") != expected["status_id"]:
            raise AdapterContractError("Displayed X status ID does not match the shortlisted source")
        if output.get("page_state") in {"login_wall", "blank_shell", "timeline", "quoted_repost"}:
            raise AdapterContractError("X exact post is not visibly open")
    if expected["kind"] == "claude_blog":
        if identity.get("path") != expected["path"]:
            raise AdapterContractError("Claude exact article path mismatch")
        if output.get("page_state") in {"generic_home", "search_page", "blank_shell"}:
            raise AdapterContractError("Claude exact article is not visibly open")
    if expected_adapter in {TRUSTED_BROWSER_ADAPTER, TRUSTED_WEB_ADAPTER}:
        required = ["browser_surface", "browser_session_boundary", "dom_text_path", "visual_capture_status"]
        missing = [field for field in required if not str(output.get(field) or "").strip()]
        if missing:
            raise AdapterContractError(f"Trusted-browser evidence missing: {', '.join(missing)}")
        visual_status = str(output.get("visual_capture_status") or "")
        if visual_status not in {"completed", "failed"}:
            raise AdapterContractError("visual_capture_status must be completed or failed")
        if visual_status == "completed" and not str(output.get("screenshot_path") or "").strip():
            raise AdapterContractError("Completed visual capture requires screenshot_path")
        if visual_status == "failed":
            if str(output.get("screenshot_path") or "").strip():
                raise AdapterContractError("Failed visual capture must not expose a fabricated screenshot_path")
            if not str(output.get("visual_capture_error") or "").strip():
                raise AdapterContractError("Failed visual capture requires visual_capture_error")
