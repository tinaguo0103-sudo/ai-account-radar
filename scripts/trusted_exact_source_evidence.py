#!/usr/bin/env python3
"""Build exact-page source-open evidence from the declared primary adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import trusted_exact_source_adapter as adapter


def build_output(candidate: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
    body = str(capture.get("visible_body") or "").strip()
    title = str(capture.get("exact_title") or "").strip()
    author = str(capture.get("author") or "").strip()
    if not title or not body or not author:
        raise adapter.AdapterContractError("Exact-page capture lacks visible title/body/author")
    primary = str(candidate.get("primary_adapter") or "")
    final_url = str(capture.get("final_url") or "")
    identity = dict(capture.get("page_identity") or adapter.expected_identity(final_url))
    content_hash = hashlib.sha256(
        json.dumps({"url": final_url, "title": title, "author": author, "body": body}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    output = {
        "protocol": "ar020d_exact_source_evidence_v1",
        "primary_adapter": primary,
        "attempted_adapters": [primary],
        "adapter_version": adapter.VERSION,
        "input_url": candidate["exact_url"],
        "exact_url": candidate["exact_url"],
        "final_url": final_url,
        "page_identity": identity,
        "identity_match": True,
        "open_status": "opened",
        "failure_reason": "",
        "page_state": capture.get("page_state", "exact_page"),
        "exact_title": title,
        "source_summary": body[:4000],
        "caption_body": body,
        "author": author,
        "platform": capture.get("platform") or ("X" if identity.get("kind") == "x_status" else "Web"),
        "publish_metadata": str(capture.get("publish_metadata") or ""),
        "source_type": capture.get("source_type", "exact_web_page"),
        "opened_at": capture.get("opened_at") or datetime.now(timezone.utc).isoformat(),
        "captured_content_hash": content_hash,
        "page_content_hash": content_hash,
        "retrieval_surface": primary,
        "browser_surface": str(capture.get("browser_surface") or ""),
        "browser_session_boundary": str(capture.get("browser_session_boundary") or ""),
        "dom_text_path": str(capture.get("dom_text_path") or ""),
        "screenshot_path": str(capture.get("screenshot_path") or ""),
        "content_evidence": [{
            "evidence_id": f"exact-page-{candidate['candidate_id']}",
            "evidence_type": "visible_exact_page",
            "text": body[:4000],
        }],
        "boundary": "one declared primary adapter; no failover or substituted content",
    }
    adapter.validate_primary_adapter(candidate, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-input", required=True)
    parser.add_argument("--capture-json", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    candidate = json.loads(Path(args.candidate_input).read_text(encoding="utf-8"))["candidate"]
    capture = json.loads(Path(args.capture_json).read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_output(candidate, capture), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
