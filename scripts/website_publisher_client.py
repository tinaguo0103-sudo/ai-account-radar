#!/usr/bin/env python3
"""Owner-only terminal snapshot publisher with runtime-only configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from publish_website_projection import (
    ProjectionError,
    build_workflow_projection,
    request_json,
)


def config_path() -> Path:
    value = os.environ.get("WEBSITE_PUBLISHER_CONFIG", "").strip()
    if not value:
        value = "output/state/website_publisher.json"
    return Path(value).expanduser().resolve()


def load_config() -> dict[str, str]:
    path = config_path()
    if not path.is_file():
        raise ProjectionError("publisher_config_missing")
    value = json.loads(path.read_text(encoding="utf-8"))
    required = ("website_url", "authority_identity", "app_bearer", "sites_bearer")
    if any(not str(value.get(key) or "").strip() for key in required):
        raise ProjectionError("publisher_config_incomplete")
    return {key: str(value[key]).strip() for key in required}


def publish_terminal(
    db_path: Path, run_id: str, *, artifact_root: Path | str | None = None,
) -> dict[str, Any]:
    config = load_config()
    payload = build_workflow_projection(
        db_path.resolve(), run_id, config["authority_identity"],
        artifact_root=artifact_root,
    )
    endpoint = config["website_url"].rstrip("/") + "/api/business-projection"
    previous_app = os.environ.get("WEBSITE_PROJECTION_BEARER")
    previous_sites = os.environ.get("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER")
    os.environ["WEBSITE_PROJECTION_BEARER"] = config["app_bearer"]
    os.environ["WEBSITE_PROJECTION_SIWC_BYPASS_BEARER"] = config["sites_bearer"]
    request_ledger = {"precondition_get": 0, "terminal_post": 0, "readback_get": 0}
    try:
        try:
            request_ledger["precondition_get"] += 1
            existing = request_json("GET", f"{endpoint}?run_id={run_id}")
        except ProjectionError as error:
            if str(error) != "business_projection_missing":
                raise
            existing = None
        if existing:
            payload["refresh_precondition"] = {
                "business_date": existing.get("business_date"),
                "authority_identity": existing.get("authority_identity"),
                "projected_at": existing.get("projected_at"),
            }
        request_ledger["terminal_post"] += 1
        result = request_json("POST", endpoint, payload)
        request_ledger["readback_get"] += 1
        readback = request_json("GET", f"{endpoint}?run_id={run_id}")
    finally:
        if previous_app is None:
            os.environ.pop("WEBSITE_PROJECTION_BEARER", None)
        else:
            os.environ["WEBSITE_PROJECTION_BEARER"] = previous_app
        if previous_sites is None:
            os.environ.pop("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER", None)
        else:
            os.environ["WEBSITE_PROJECTION_SIWC_BYPASS_BEARER"] = previous_sites
    expected = {
        "content": len(payload["collected_items"]),
        "topics": len(payload["topics"]),
        "scripts": len(payload["scripts"]),
    }
    def exact_rows(expected_rows: list[dict[str, Any]], actual_rows: Any, keys: tuple[str, ...]) -> bool:
        if not isinstance(actual_rows, list) or len(actual_rows) != len(expected_rows):
            return False
        expected_by_id = {str(row.get("id") or ""): row for row in expected_rows}
        actual_by_id = {str(row.get("id") or ""): row for row in actual_rows if isinstance(row, dict)}
        if len(expected_by_id) != len(expected_rows) or len(actual_by_id) != len(actual_rows):
            return False
        if expected_by_id.keys() != actual_by_id.keys():
            return False
        return all(
            all(expected_by_id[row_id].get(key) == actual_by_id[row_id].get(key) for key in keys)
            for row_id in expected_by_id
        )

    article_keys = (
        "id", "run_id", "topic_id", "source_topic_id", "title", "body",
        "artifact_path", "article_sha256", "artifact_sha256", "claim_review",
    )
    script_keys = (
        "id", "run_id", "topic_id", "script_version", "title", "hook",
        "content_structure", "body", "updated_at", "current_revision_number", "saved_at",
    )
    if (
        readback.get("run_id") != payload["run_id"]
        or readback.get("business_date") != payload["business_date"]
        or readback.get("run_status") != payload["run"]["status"]
        or readback.get("counts") != expected
        or readback.get("article_count") != len(payload["articles"])
        or readback.get("authority_identity") != config["authority_identity"]
        or not exact_rows(payload["articles"], readback.get("articles"), article_keys)
        or not exact_rows(payload["scripts"], readback.get("scripts"), script_keys)
    ):
        raise ProjectionError("business_projection_readback_mismatch")
    return {
        "result": result, "readback": readback, "payload": payload,
        "request_ledger": request_ledger,
    }
