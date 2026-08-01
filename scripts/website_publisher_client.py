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


def publish_terminal(db_path: Path, run_id: str) -> dict[str, Any]:
    config = load_config()
    payload = build_workflow_projection(
        db_path.resolve(), run_id, config["authority_identity"],
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
    if (
        readback.get("run_id") != payload["run_id"]
        or readback.get("business_date") != payload["business_date"]
        or readback.get("run_status") != payload["run"]["status"]
        or readback.get("counts") != expected
        or readback.get("authority_identity") != config["authority_identity"]
    ):
        raise ProjectionError("business_projection_readback_mismatch")
    return {
        "result": result, "readback": readback, "payload": payload,
        "request_ledger": request_ledger,
    }
