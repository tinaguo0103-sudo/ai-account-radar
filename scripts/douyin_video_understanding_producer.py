#!/usr/bin/env python3
"""Product-owned Douyin discovery, OCR-first understanding, and cleanup runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from douyin_video_understanding import (
    apply_policy_decisions,
    budget_selection,
    canonical,
    digest,
    fold_near_duplicates,
    merge_candidates,
)

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = ROOT / "scripts/douyin_video_discovery.mjs"
MEDIA_RESOLVER = ROOT / "scripts/douyin_video_media_resolver.mjs"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36"
)
RUNTIME_CONFIG_ENV = "DOUYIN_VIDEO_RUNTIME_CONFIG"


class ProducerError(RuntimeError):
    pass


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def atomic_json(path: Path, payload: Any) -> None:
    encoded = (canonical(payload) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == encoded:
            return
        raise ProducerError("producer_artifact_conflict")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)
    if path.read_bytes() != encoded:
        raise ProducerError("producer_artifact_readback_unknown")


def validate_runtime(config: dict[str, Any]) -> dict[str, Path]:
    executables = {
        "ffmpeg": "video_ffmpeg",
        "vision_ocr_binary": "video_vision_runtime",
        "sensevoice_python": "video_asr_runtime",
    }
    models = {
        "sensevoice_model": (
            "video_asr_model",
            ("model.pt", "config.yaml", "am.mvn", "tokens.json",
             "chn_jpn_yue_eng_ko_spectok.bpe.model"),
        ),
        "fsmn_vad_model": (
            "video_vad_model",
            ("model.pt", "config.yaml", "am.mvn"),
        ),
    }
    output: dict[str, Path] = {}
    for key, prefix in executables.items():
        raw = str(config.get(key) or "").strip()
        value = Path(raw).expanduser()
        if not raw:
            raise ProducerError(f"{prefix}_missing")
        if value.is_symlink() and not value.exists():
            raise ProducerError(f"{prefix}_broken_symlink")
        if not value.exists():
            raise ProducerError(f"{prefix}_missing")
        if not value.is_file():
            raise ProducerError(f"{prefix}_not_file")
        if not os.access(value, os.X_OK):
            raise ProducerError(f"{prefix}_not_executable")
        # Virtualenv Python launchers must retain their symlink path so Python
        # discovers the venv site-packages instead of the base interpreter.
        output[key] = value.absolute() if key.endswith("_python") else value.resolve()
    for key, (prefix, required_files) in models.items():
        raw = str(config.get(key) or "").strip()
        value = Path(raw).expanduser()
        if not raw or not value.exists():
            raise ProducerError(f"{prefix}_missing")
        if not value.is_dir():
            raise ProducerError(f"{prefix}_not_directory")
        for relative in required_files:
            required = value / relative
            if not required.is_file() or not os.access(required, os.R_OK):
                raise ProducerError(f"{prefix}_required_file_missing:{relative}")
            if required.stat().st_size == 0:
                raise ProducerError(f"{prefix}_required_file_empty:{relative}")
        output[key] = value.resolve()
    timeout = float(config.get("readiness_probe_timeout_seconds") or 20)
    if timeout <= 0 or timeout > 60:
        raise ProducerError("video_runtime_probe_timeout_invalid")
    _probe_runtime(
        [str(output["ffmpeg"]), "-version"],
        timeout,
        "video_ffmpeg",
        lambda result: "ffmpeg version" in (result.stdout + result.stderr).lower(),
    )
    _probe_runtime(
        [str(output["vision_ocr_binary"])],
        timeout,
        "video_vision_runtime",
        lambda result: result.stdout.strip() == "[]",
    )
    _probe_runtime(
        [
            str(output["sensevoice_python"]), "-c",
            "import funasr; from funasr import AutoModel; print('sensevoice_runtime_ready')",
        ],
        timeout,
        "video_asr_runtime",
        lambda result: result.stdout.strip().endswith("sensevoice_runtime_ready"),
    )
    return output


def _probe_runtime(
    command: list[str],
    timeout: float,
    prefix: str,
    validate_output: Any,
) -> None:
    try:
        result = subprocess.run(
            command, text=True, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ProducerError(f"{prefix}_probe_timeout") from error
    except OSError as error:
        raise ProducerError(f"{prefix}_probe_unavailable") from error
    if result.returncode != 0:
        raise ProducerError(f"{prefix}_probe_nonzero")
    if not validate_output(result):
        raise ProducerError(f"{prefix}_probe_invalid_output")


def policy_decisions(
    candidates: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    policy = policy or {}
    eligible = [
        row for row in candidates
        if row.get("likes") is not None and (
            row.get("published_at")
            or row.get("published_recency", {}).get("minimum_seconds") is not None
        )
    ]
    totals = sorted(int(row.get("likes") or 0) for row in eligible)
    percentile = float(policy.get("relative_engagement_percentile", 0.6))
    relative_floor = totals[max(0, int(len(totals) * percentile) - 1)] if totals else 0
    title_patterns = list(policy.get("title_value_patterns") or [
        r"\bAI\b", "人工智能", "模型", "Agent", "Prompt", "工作流", "智能体", "豆包", "Claude", "GPT",
    ])
    title_expression = "|".join(f"(?:{value})" for value in title_patterns)
    exploration_limit = int(policy.get("exploration_limit", 2))
    exploration_used = 0
    output = []
    for row in candidates:
        title_value = bool(re.search(title_expression, str(row.get("title") or ""), re.I))
        total = int(row.get("likes") or 0)
        engagement = bool(row in eligible and totals and total > 0 and total >= relative_floor)
        exploration = (
            row.get("discovery_source") == "dynamic_search"
            and not title_value and not engagement and exploration_used < exploration_limit
        )
        if exploration:
            exploration_used += 1
        reasons = [
            reason for reason, enabled in (
                ("title_value", title_value),
                ("engagement_relative", engagement),
                ("exploration", exploration),
            ) if enabled
        ]
        output.append({
            "candidate_id": f"douyin:{row['aweme_id']}",
            "selected": bool(reasons),
            "reasons": reasons,
            "evidence": {
                "likes_recency_relative": engagement,
                "complete_interaction": engagement and all(
                    row.get(key) is not None for key in ("comments", "favorites", "shares")
                ),
                "title_value": title_value,
                "persona_fit": False,
                "exploration": exploration,
            },
            "explanation": (
                "点赞与时效证据、标题价值或探索任一成立；"
                "不使用双门槛或固定点赞阈值，点赞证据不冒充完整互动热门。"
            ),
        })
    return output


def download(
    url: str,
    destination: Path,
    timeout: int,
    max_bytes: int,
    referer: str,
) -> dict[str, Any]:
    if not url.startswith(("https://", "http://")):
        raise ProducerError("video_media_url_invalid")
    if not re.fullmatch(r"https://www\.douyin\.com/video/\d+", referer):
        raise ProducerError("video_media_referer_invalid")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": referer},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as out:
            if getattr(response, "status", 200) != 200:
                raise ProducerError("video_media_fetch_failed")
            started = time.monotonic()
            written = 0
            while True:
                if time.monotonic() - started > timeout:
                    raise ProducerError("video_media_fetch_deadline_exceeded")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ProducerError("video_media_size_limit_exceeded")
                out.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ProducerError(f"video_media_fetch_failed:{type(error).__name__}") from error
    if not destination.exists() or not destination.stat().st_size:
        raise ProducerError("video_media_empty")
    return {"sha256": file_hash(destination), "bytes": destination.stat().st_size}


def command(
    args: list[str],
    error: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, env=env)
    if result.returncode != 0:
        raise ProducerError(f"{error}:{result.returncode}")
    return result


def dedupe_ocr(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output, prior = [], ""
    for row in rows:
        text = compact(str(row.get("text") or ""))
        if not text or text == prior:
            continue
        match = re.search(r"(\d+)$", Path(str(row["path"])).stem)
        second = max(0, int(match.group(1)) - 1) if match else len(output)
        output.append({
            "start": second,
            "end": second + 1,
            "text": text,
            "frame_sha256": file_hash(Path(row["path"])),
        })
        prior = text
    return output


def screen_facts(title: str, timeline: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = "\n".join(row["text"] for row in timeline)
    output: list[dict[str, Any]] = []
    urls = re.findall(r"(?:https?://|www\.)[^\s]+", text, re.I)
    output.extend({"kind": "url", "value": value, "verified": False} for value in urls[:8])
    tools = sorted(set(re.findall(
        r"\b(?:AI|AIGC|Agent|Prompt|WorkBuddy|OpenBear|LibTV|Claude|ChatGPT|GPT-\w+)\b|豆包",
        f"{title}\n{text}", re.I,
    )))
    output.extend({"kind": "tool_name", "value": value, "verified": True} for value in tools)
    output.extend({
        "kind": "number", "value": value, "verified": True,
    } for value in sorted(set(re.findall(r"\b\d+(?:\.\d+)?\b", text)))[:30])
    if re.search(r"(?:提示词|prompt)", text, re.I):
        output.append({"kind": "prompt", "value": "screen_prompt_present", "verified": False})
    if re.search(r"(?:代码|python|javascript|json|yaml|skill\.md)", text, re.I):
        output.append({"kind": "code", "value": "screen_code_present", "verified": False})
    for name, value in re.findall(
        r"([A-Za-z_][A-Za-z0-9_.-]{1,30})\s*[:=]\s*([A-Za-z0-9_.:/-]{1,80})",
        text,
    )[:20]:
        output.append({
            "kind": "parameter",
            "value": f"{name}={value}",
            "verified": True,
        })
    unresolved = []
    if re.search(r"(?:https?://|www\.)", text, re.I) and not urls:
        unresolved.append({"term": "incomplete_url", "reason": "ocr_url_incomplete"})
    ascii_terms = re.findall(r"\b[A-Za-z][A-Za-z0-9.-]{2,}\b", text)
    if ascii_terms and not tools:
        unresolved.append({"term": "english_proper_noun", "reason": "ocr_asr_confirmation_required"})
    return output, unresolved


def asr_worker(config: dict[str, Any], audio: Path) -> dict[str, Any]:
    result_path = audio.with_suffix(".sensevoice.json")
    runtime_bin = audio.parent / "runtime_bin"
    runtime_bin.mkdir()
    (runtime_bin / "ffmpeg").symlink_to(Path(config["ffmpeg"]))
    env = os.environ.copy()
    env["PATH"] = f"{runtime_bin}{os.pathsep}{env.get('PATH', '')}"
    args = [
        str(config["sensevoice_python"]), str(Path(__file__).resolve()), "asr-worker",
        "--audio", str(audio), "--result", str(result_path),
        "--model", str(config["sensevoice_model"]), "--vad", str(config["fsmn_vad_model"]),
    ]
    command(args, "video_asr_failed", env=env)
    return json.loads(result_path.read_text())


def full_worker(config: dict[str, Any], audio: Path) -> dict[str, Any] | None:
    python = Path(str(config.get("whisper_python") or ""))
    model = str(config.get("whisper_model") or "")
    if not python.exists() or not model:
        return None
    result_path = audio.with_suffix(".full.json")
    command([
        str(python), str(Path(__file__).resolve()), "whisper-worker",
        "--audio", str(audio), "--result", str(result_path), "--model", model,
    ], "video_full_asr_failed")
    return json.loads(result_path.read_text())


def process_one(
    candidate: dict[str, Any],
    *,
    run_id: str,
    config: dict[str, Any],
    runtime: dict[str, Path],
    work_root: Path,
    keyframe_root: Path,
    trigger: str,
) -> dict[str, Any]:
    aweme_id = candidate["aweme_id"]
    work = work_root / aweme_id
    work.mkdir(parents=True, exist_ok=False)
    media = work / "video.mp4"
    audio_media = work / "audio-media.mp4"
    audio = work / "audio.wav"
    frames = work / "frames"
    frames.mkdir()
    package: dict[str, Any]
    try:
        media_info = download(
            str(candidate.get("playable_url") or ""),
            media,
            int(config.get("timeout", 90)),
            int(config.get("maximum_media_bytes", 300 * 1024 * 1024)),
            str(candidate.get("source_url") or ""),
        )
        audio_info = None
        audio_input = media
        if candidate.get("audio_url"):
            audio_info = download(
                str(candidate["audio_url"]),
                audio_media,
                int(config.get("timeout", 90)),
                int(config.get("maximum_media_bytes", 300 * 1024 * 1024)),
                str(candidate.get("source_url") or ""),
            )
            audio_input = audio_media
        command([
            str(runtime["ffmpeg"]), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(audio_input), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(audio),
        ], "video_audio_extract_failed")
        command([
            str(runtime["ffmpeg"]), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(media), "-vf", "fps=1,scale=960:-2", str(frames / "frame_%05d.jpg"),
        ], "video_frame_extract_failed")
        frame_paths = sorted(frames.glob("*.jpg"))
        if not frame_paths:
            raise ProducerError("video_frames_empty")
        ocr = command(
            [str(runtime["vision_ocr_binary"]), *map(str, frame_paths)],
            "video_ocr_failed",
        )
        timeline = dedupe_ocr(json.loads(ocr.stdout))
        asr = asr_worker({**config, **runtime}, audio)
        screen_text, unresolved = screen_facts(candidate["title"], timeline)
        full = full_worker(config, audio) if unresolved else None
        if full:
            unresolved = [
                {**row, "full_large_v3_text_sha256": full["text_sha256"]}
                for row in unresolved
            ]
        keyframes = []
        for index in sorted({0, len(frame_paths) // 2}):
            source = frame_paths[index]
            destination = keyframe_root / f"{aweme_id}_{source.name}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            keyframes.append({
                "time_second": index,
                "path": str(destination),
                "sha256": file_hash(destination),
            })
        package = {
            "run_id": run_id,
            "aweme_id": aweme_id,
            "source_url": candidate["source_url"],
            "author": candidate["author"],
            "title": candidate["title"],
            "published_at": candidate["published_at"],
            "public_engagement": {
                key: candidate.get(key) for key in ("likes", "comments", "favorites", "shares")
            },
            "discovery_source": candidate["discovery_source"],
            "status": "completed_with_failures" if unresolved else "completed",
            "caption_timeline": timeline,
            "ocr": {
                "engine": "macOS Vision accurate zh-Hans/en-US",
                "binary_sha256": file_hash(runtime["vision_ocr_binary"]),
                "frame_rate_fps": 1,
                "frame_count": len(frame_paths),
                "media_sha256": media_info["sha256"],
                "audio_media_sha256": (
                    audio_info["sha256"] if audio_info else media_info["sha256"]
                ),
            },
            "asr": asr,
            "full_large_v3_fallback": full,
            "screen_text": screen_text,
            "keyframes": keyframes,
            "unresolved_terms": unresolved,
            "failures": (
                [{"type": "screen_text_unresolved", "count": len(unresolved)}] if unresolved else []
            ),
            "trigger": trigger,
            "substitute_count": 0,
            "temporary_media_remaining": 0,
            "runtime_provenance": {
                "producer_sha256": file_hash(Path(__file__).resolve()),
                "ffmpeg_sha256": file_hash(runtime["ffmpeg"]),
                "vision_source_sha256": file_hash(ROOT / "scripts/douyin_video_vision_ocr.swift"),
                "sensevoice_model": str(runtime["sensevoice_model"]),
                "fsmn_vad_model": str(runtime["fsmn_vad_model"]),
            },
        }
        package["input_identity_sha256"] = digest({
            "run_id": run_id,
            "candidate_raw_identity": candidate["raw_identity"],
            "media_sha256": media_info["sha256"],
            "audio_media_sha256": (
                audio_info["sha256"] if audio_info else media_info["sha256"]
            ),
            "runtime": package["runtime_provenance"],
        })
    except (OSError, ValueError, json.JSONDecodeError, ProducerError) as error:
        package = {
            "run_id": run_id,
            "aweme_id": aweme_id,
            "source_url": candidate["source_url"],
            "status": "failed",
            "failure": str(error),
            "trigger": trigger,
            "substitute_count": 0,
            "temporary_media_remaining": 0,
        }
    except Exception as error:
        package = {
            "run_id": run_id,
            "aweme_id": aweme_id,
            "source_url": candidate["source_url"],
            "status": "failed",
            "failure": f"video_candidate_unexpected:{type(error).__name__}",
            "trigger": trigger,
            "substitute_count": 0,
            "temporary_media_remaining": 0,
        }
    finally:
        try:
            shutil.rmtree(work)
        except OSError as error:
            package["status"] = "failed"
            package["failure"] = f"video_cleanup_failed:{type(error).__name__}"
            package["temporary_media_remaining"] = 1
    return package


def load_discovery_payload(
    args: argparse.Namespace,
    run_id: str,
    output_root: Path,
) -> dict[str, Any]:
    discovery_path = output_root / run_id / "video_producer" / "discovery.json"
    mode = getattr(args, "mode", "") or args.video_mode
    if discovery_path.is_file():
        payload = json.loads(discovery_path.read_text())
    elif mode == "normal":
        query = str(getattr(args, "search_query", "") or "AI 工具 人工智能")
        result = subprocess.run([
            "node", str(DISCOVERY), "--output", str(discovery_path),
            "--cdp", args.cdp, "--query", query,
        ], text=True, capture_output=True)
        if result.returncode:
            try:
                error = json.loads(result.stdout).get("error")
            except (ValueError, AttributeError):
                error = "discovery_failed"
            atomic_json(
                discovery_path.with_name("discovery.failure.json"),
                {
                    "run_id": run_id,
                    "status": "failed",
                    "failure": str(error or "discovery_failed"),
                    "substitute_count": 0,
                },
            )
            raise ProducerError(str(error or "discovery_failed"))
        payload = json.loads(discovery_path.read_text())
    elif mode == "qa-fixture":
        if not args.discovery_fixture:
            raise ProducerError("video_discovery_fixture_missing")
        payload = json.loads(Path(args.discovery_fixture).read_text())
        atomic_json(discovery_path, payload)
    else:
        raise ProducerError("video_producer_mode_invalid")
    if (payload.get("status") or payload.get("source_global_status")) != "completed":
        raise ProducerError("video_discovery_not_completed")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ProducerError("video_discovery_candidates_invalid")
    for row in candidates:
        row["run_id"] = run_id
    return payload


def load_discovery(args: argparse.Namespace, run_id: str, output_root: Path) -> list[dict[str, Any]]:
    return load_discovery_payload(args, run_id, output_root)["candidates"]


def produce(
    args: argparse.Namespace,
    *,
    on_demand_ids: set[str] | None = None,
    discovered_candidates: list[dict[str, Any]] | None = None,
    include_automatic: bool = True,
) -> dict[str, Any]:
    runtime_config = (
        getattr(args, "runtime_config", "")
        or getattr(args, "video_runtime_config", "")
        or os.environ.get(RUNTIME_CONFIG_ENV, "")
    )
    if not str(runtime_config).strip():
        raise ProducerError("video_runtime_config_missing")
    config = json.loads(Path(runtime_config).read_text())
    runtime = validate_runtime(config)
    output_root = Path(getattr(args, "output_root", "") or args.artifact_root).resolve()
    candidates = (
        load_discovery(args, args.run_id, output_root)
        if discovered_candidates is None
        else discovered_candidates
    )
    merged = merge_candidates([candidates], args.run_id)
    policy_path = (
        getattr(args, "video_policy", "")
        or getattr(args, "policy", "")
        or config.get("policy_path", "")
    )
    if not policy_path:
        raise ProducerError("video_policy_missing")
    policy = json.loads(Path(policy_path).read_text())
    decisions = policy_decisions(candidates, policy)
    plan = budget_selection(
        fold_near_duplicates(apply_policy_decisions(merged, decisions, policy)),
        policy,
    )
    automatic = {
        row["id"] for row in plan["selected"]
    } if include_automatic else set()
    selected = automatic | (on_demand_ids or set())
    by_id = {f"douyin:{row['aweme_id']}": row for row in candidates}
    mode = getattr(args, "mode", "") or args.video_mode
    if mode == "normal" and selected:
        producer_root = output_root / args.run_id / "video_producer"
        resolver_input = producer_root / (
            "media_resolver_input.json" if include_automatic
            else f"media_resolver_input_{digest(sorted(selected))[:16]}.json"
        )
        resolver_output = producer_root / (
            "media_resolver.json" if include_automatic
            else f"media_resolver_{digest(sorted(selected))[:16]}.json"
        )
        atomic_json(resolver_input, [by_id[identity] for identity in sorted(selected) if identity in by_id])
        if not resolver_output.is_file():
            resolution = subprocess.run([
                "node", str(MEDIA_RESOLVER), "--cdp", args.cdp,
                "--input", str(resolver_input), "--output", str(resolver_output),
            ], text=True, capture_output=True)
            if resolution.returncode:
                try:
                    error = json.loads(resolution.stdout).get("error")
                except (ValueError, AttributeError):
                    error = "media_resolution_failed"
                raise ProducerError(str(error or "media_resolution_failed"))
        resolved = json.loads(resolver_output.read_text())
        if resolved.get("status") != "completed":
            raise ProducerError("media_resolution_not_completed")
        for candidate in resolved.get("candidates") or []:
            by_id[f"douyin:{candidate['aweme_id']}"] = candidate
    work_root = output_root / args.run_id / "video_producer" / "work"
    keyframe_root = output_root / args.run_id / "video_understanding" / "keyframes"
    checkpoint_root = output_root / args.run_id / "video_understanding" / "packages"
    aggregate_path = (
        output_root / args.run_id / "video_producer"
        / ("packages.json" if include_automatic
           else f"packages_on_demand_{digest(sorted(on_demand_ids or set()))[:16]}.json")
    )
    aggregate_by_id: dict[str, dict[str, Any]] = {}
    if aggregate_path.is_file():
        aggregate = json.loads(aggregate_path.read_text())
        if not isinstance(aggregate, list):
            raise ProducerError("video_package_checkpoint_invalid")
        aggregate_by_id = {
            f"douyin:{row.get('aweme_id')}": row for row in aggregate
            if isinstance(row, dict)
        }
        if set(aggregate_by_id) != selected:
            raise ProducerError("video_package_checkpoint_identity_conflict")
    work_root.mkdir(parents=True, exist_ok=True)
    packages = []
    cleanup_blocked = False
    for identity in sorted(selected):
        if cleanup_blocked:
            break
        candidate = by_id.get(identity)
        if not candidate:
            packages.append({
                "run_id": args.run_id, "aweme_id": identity.removeprefix("douyin:"),
                "source_url": "", "status": "failed", "failure": "on_demand_candidate_missing",
                "substitute_count": 0, "temporary_media_remaining": 0,
            })
            continue
        checkpoint = checkpoint_root / f"{candidate['aweme_id']}.json"
        if identity in aggregate_by_id:
            package = aggregate_by_id[identity]
        elif checkpoint.is_file():
            package = json.loads(checkpoint.read_text())
            if (
                package.get("run_id") != args.run_id
                or package.get("aweme_id") != candidate["aweme_id"]
                or package.get("source_url") != candidate["source_url"]
            ):
                raise ProducerError("video_package_checkpoint_identity_conflict")
        else:
            package = process_one(
                candidate,
                run_id=args.run_id,
                config=config,
                runtime=runtime,
                work_root=work_root,
                keyframe_root=keyframe_root,
                trigger="on_demand" if identity in (on_demand_ids or set()) else "automatic",
            )
            atomic_json(checkpoint, package)
        packages.append(package)
        cleanup_blocked = package.get("temporary_media_remaining") != 0
    if work_root.exists() and any(work_root.iterdir()):
        raise ProducerError("video_cleanup_incomplete")
    result = {
        "candidates": merged,
        "raw_candidates": candidates,
        "decisions": decisions,
        "packages": packages,
        "plan": plan,
        "failures": [
            {"candidate_id": f"douyin:{row.get('aweme_id')}", "failure": row.get("failure")}
            for row in packages if row.get("status") == "failed"
        ],
    }
    producer_root = output_root / args.run_id / "video_producer"
    atomic_json(producer_root / "candidates.json", [candidates])
    atomic_json(producer_root / "decisions.json", decisions)
    atomic_json(aggregate_path, packages)
    return result


def worker_main(args: argparse.Namespace) -> int:
    if args.worker == "asr-worker":
        try:
            from funasr import AutoModel
        except Exception as error:
            raise ProducerError("video_asr_runtime_missing") from error
        model = AutoModel(
            model=args.model, vad_model=args.vad,
            vad_kwargs={"max_single_segment_time": 30000}, disable_update=True,
        )
        rows = model.generate(
            input=args.audio, cache={}, language="auto", use_itn=True,
            batch_size_s=300, merge_vad=True, merge_length_s=15,
        )
        text = compact(" ".join(str(row.get("text") or "") for row in rows))
        atomic_json(Path(args.result), {
            "primary_model": "iic/SenseVoiceSmall",
            "vad_model": "fsmn-vad",
            "max_single_segment_time": 30000,
            "merge_vad": True,
            "merge_length_s": 15,
            "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        })
        return 0
    try:
        import mlx_whisper
    except Exception as error:
        raise ProducerError("video_full_asr_runtime_missing") from error
    result = mlx_whisper.transcribe(
        args.audio, path_or_hf_repo=args.model, language=None,
        condition_on_previous_text=True, word_timestamps=True, verbose=False,
    )
    text = compact(str(result.get("text") or ""))
    atomic_json(Path(args.result), {
        "model": args.model,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "segments": result.get("segments") or [],
    })
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    sub = value.add_subparsers(dest="worker")
    for name in ("asr-worker", "whisper-worker"):
        worker = sub.add_parser(name)
        worker.add_argument("--audio", required=True)
        worker.add_argument("--result", required=True)
        worker.add_argument("--model", required=True)
        if name == "asr-worker":
            worker.add_argument("--vad", required=True)
    value.add_argument("--run-id")
    value.add_argument("--business-date")
    value.add_argument("--runtime-config")
    value.add_argument("--policy")
    value.add_argument("--output-root")
    value.add_argument("--mode", choices=("normal", "qa-fixture"), default="normal")
    value.add_argument("--discovery-fixture", default="")
    value.add_argument("--cdp", default="http://127.0.0.1:9333")
    value.add_argument("--search-query", default="AI")
    value.add_argument("--on-demand", action="append", default=[])
    value.add_argument("--check-only", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.worker:
            return worker_main(args)
        args.runtime_config = (
            args.runtime_config or os.environ.get(RUNTIME_CONFIG_ENV, "")
        )
        if not str(args.runtime_config).strip():
            raise ProducerError("video_runtime_config_missing")
        config = json.loads(Path(args.runtime_config).read_text())
        policy_path = str(config.get("policy_path") or "").strip()
        if not policy_path:
            raise ProducerError("video_policy_missing")
        policy = json.loads(Path(policy_path).read_text())
        if not isinstance(policy, dict):
            raise ProducerError("video_policy_invalid")
        runtime = validate_runtime(config)
        if args.check_only:
            print(json.dumps({
                "ok": True,
                "status": "ready",
                "runtime": sorted(runtime),
            }))
            return 0
        result = produce(args, on_demand_ids=set(args.on_demand))
        print(json.dumps({
            "ok": True,
            "candidate_count": len(result["candidates"]),
            "package_count": len(result["packages"]),
            "failure_count": len(result["failures"]),
        }))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ProducerError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
