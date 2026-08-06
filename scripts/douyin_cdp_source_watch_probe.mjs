#!/usr/bin/env node
// Low-frequency Douyin homepage probe via Chrome DevTools Protocol.
//
// Boundaries:
// - no Feishu writes;
// - no cookies/tokens/profile export;
// - no comments, no downloads, no full-history crawl;
// - requires the user to run Chrome with remote debugging and log in manually.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DEFAULT_CONFIG = path.join(ROOT, "config/content_sources.yaml");
const DEFAULT_OUT = path.join(ROOT, "output/spikes/douyin_cdp_source_watch_probe");
const DEFAULT_CDP = "http://127.0.0.1:9333";
const RESOLVER = path.join(ROOT, "scripts/url_content_resolver.py");

export function parseArgs(args = process.argv.slice(2)) {
  const options = {
    config: DEFAULT_CONFIG,
    outDir: DEFAULT_OUT,
    cdp: DEFAULT_CDP,
    accountLimit: "0",
    videoLimit: 3,
    scanLimit: 10,
    seenLedger: path.join(ROOT, "output/state/douyin_seen_items.json"),
    lifecycleLedger: path.join(ROOT, "output/state/douyin_candidate_lifecycle.json"),
    waitMs: 7000,
    batchSize: 5,
    accountPacingMs: 10000,
    batchCooldownMs: 120000,
    tailRetryDelayMs: 600000,
    sourceDb: path.join(ROOT, "output/state/source_control.sqlite3"),
    checkOnly: false,
    worksFactsProof: false,
  };
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--config") options.config = args[++i];
    else if (arg === "--out-dir") options.outDir = args[++i];
    else if (arg === "--cdp") options.cdp = args[++i];
    else if (arg === "--account-limit") options.accountLimit = args[++i];
    else if (arg === "--video-limit") options.videoLimit = Number(args[++i]);
    else if (arg === "--scan-limit") options.scanLimit = Number(args[++i]);
    else if (arg === "--seen-ledger") options.seenLedger = args[++i];
    else if (arg === "--lifecycle-ledger") options.lifecycleLedger = args[++i];
    else if (arg === "--wait-ms") options.waitMs = Number(args[++i]);
    else if (arg === "--account-pacing-ms") options.accountPacingMs = Number(args[++i]);
    else if (arg === "--batch-size") options.batchSize = Number(args[++i]);
    else if (arg === "--batch-cooldown-ms") options.batchCooldownMs = Number(args[++i]);
    else if (arg === "--tail-retry-delay-ms") options.tailRetryDelayMs = Number(args[++i]);
    else if (arg === "--source-db") options.sourceDb = args[++i];
    else if (arg === "--check-only") options.checkOnly = true;
    else if (arg === "--works-facts-proof") options.worksFactsProof = true;
  }
  return options;
}

export function validateFullAccountLimitArgs(args = process.argv.slice(2)) {
  const matches = [];
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index];
    if (token === "--account-limit") {
      const requested = args[index + 1];
      if (requested === undefined || String(requested).startsWith("--")) {
        return { ok: false, requested: "", reason: "missing_account_limit_value" };
      }
      matches.push(String(requested));
    } else if (String(token).startsWith("--account-limit=")) {
      return {
        ok: false,
        requested: String(token).split("=", 2)[1] || "",
        reason: "account_limit_alias_rejected",
      };
    }
  }
  if (matches.length > 1) {
    return { ok: false, requested: matches.join(","), reason: "duplicate_account_limit" };
  }
  const requested = matches.length ? matches[0] : "0";
  if (requested !== "0") {
    return { ok: false, requested, reason: "full_account_collection_requires_exact_zero" };
  }
  return { ok: true, requested, value: 0, reason: "" };
}

export function limitedPlanRejection(gate) {
  return {
    ok: false,
    status: "limited_plan_rejected",
    reason: gate.reason,
    layer: "douyin_cdp_source_watch_probe",
    requested_account_limit: gate.requested,
    side_effects_started: false,
    env_loaded: false,
    writes_feishu: false,
    cache_accessed: false,
    chrome_contacted: false,
    collection_started: false,
    notification_sent: false,
  };
}

export function loadSources(configPath) {
  const text = fs.readFileSync(configPath, "utf8");
  return JSON.parse(text).sources || [];
}

export function selectedSources(sources) {
  const roles = new Set(["current_main_competitor", "current_aux_competitor"]);
  return sources
    .filter((source) => source.platform === "抖音"
      && roles.has(source.source_role)
      && source.default_enabled !== false
      && source.participates_main_sampling !== false)
}

export function validateDouyinSourceIdentity(source) {
  const name = String(source.account_name || source.name || "").trim();
  const homepage = String(source.url || source.homepage_url || "").trim();
  if (!name) return { ok: false, failure_code: "douyin_account_name_missing", name, homepage, identity: "" };
  let parsed;
  try {
    parsed = new URL(homepage);
  } catch {
    return { ok: false, failure_code: "douyin_configured_account_url_invalid", name, homepage, identity: "" };
  }
  const host = parsed.hostname.toLowerCase();
  const identity = configuredAccountIdentity(homepage);
  if (!["douyin.com", "www.douyin.com"].includes(host) || !identity) {
    return {
      ok: false,
      failure_code: host.endsWith("douyin.com")
        ? "douyin_configured_account_identity_missing"
        : "douyin_configured_account_wrong_platform",
      name,
      homepage,
      identity: "",
    };
  }
  return { ok: true, failure_code: "", name, homepage, identity };
}

export function validateSourcePlan(sources) {
  const inspected = sources.map((source) => ({ source, ...validateDouyinSourceIdentity(source) }));
  const identityCounts = new Map();
  for (const row of inspected.filter((item) => item.ok)) {
    identityCounts.set(row.identity, (identityCounts.get(row.identity) || 0) + 1);
  }
  for (const row of inspected) {
    if (row.ok && identityCounts.get(row.identity) > 1) {
      row.ok = false;
      row.failure_code = "douyin_configured_account_identity_duplicate";
    }
  }
  const valid = inspected.filter((item) => item.ok);
  const invalid = inspected.filter((item) => !item.ok);
  return {
    ok: valid.length > 0,
    planned_accounts: sources.length,
    executable_accounts: valid.length,
    invalid_account_count: invalid.length,
    account_names: inspected.map((item) => item.name),
    executable_account_names: valid.map((item) => item.name),
    valid_sources: valid.map((item) => item.source),
    invalid_accounts: invalid.map((item) => ({
      account_name: item.name,
      homepage_url: item.homepage,
      failure_code: item.failure_code,
      action_required: true,
      action: "在 Feishu 01 补充可信 douyin.com/user/<sec_user_id> 主页，或停用该来源；不得猜测身份。",
    })),
  };
}

export function isTransientAccountFailure(row) {
  return row?.status === "failed"
    && row?.extraction_diagnostics?.failure_code === "douyin_works_response_timeout"
    && !row?.shared_runtime_failure;
}

export function sourceGlobalRisk(row, priorRows = []) {
  if (row?.source_global_risk) return String(row.source_global_risk);
  if (row?.status === "needs_login_or_verification") return "verification_required";
  const code = String(row?.extraction_diagnostics?.failure_code || "");
  if (["verification_required", "logged_out", "challenge_detected", "sms_verification_required"].includes(code)) {
    return code;
  }
  const schemaFailures = [...priorRows, row].filter((item) =>
    ["douyin_works_response_schema_invalid", "douyin_works_response_body_invalid"].includes(
      String(item?.extraction_diagnostics?.failure_code || ""),
    ));
  return schemaFailures.length >= 2 ? "repeated_cross_account_xhr_failure" : "";
}

export async function probeSourcesWithTailRetry(client, sources, options, probe = probeAccount, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))) {
  const rows = [];
  let sharedRuntimeFailure = null;
  let riskSignal = "";
  const completedIds = new Set(options.completedSourceIds || []);
  const batchSize = Math.max(1, Number(options.batchSize) || 5);
  for (const [sourceIndex, source] of sources.entries()) {
    const sourceId = String(source.id || source.source_id || "");
    if (completedIds.has(sourceId)) continue;
    if (riskSignal) {
      rows.push({
        source_id: sourceId,
        account_name: source.account_name || source.name || "",
        homepage_url: source.url || source.homepage_url || "",
        status: "not_attempted_waiting_manual_verification",
        failure_reason: riskSignal,
        artifact_count: 0,
        video_ids: [],
        video_links: [],
      });
      continue;
    }
    if (sharedRuntimeFailure) {
      rows.push({
        source_id: sourceId,
        account_name: source.account_name || source.name || "",
        homepage_url: source.url || source.homepage_url || "",
        status: "not_attempted_source_runtime_failure",
        failure_reason: sharedRuntimeFailure.reason,
        artifact_count: 0,
        video_ids: [],
        video_links: [],
      });
      continue;
    }
    const row = await probe(client, source, options);
    row.source_id = sourceId;
    row.attempts = 1;
    rows.push(row);
    riskSignal = sourceGlobalRisk(row, rows.filter((_item, index) => index < rows.length - 1));
    if (riskSignal) {
      await options.onCheckpoint?.(rows, riskSignal);
      continue;
    }
    await options.onCheckpoint?.(rows, "");
    if (row.shared_runtime_failure) {
      sharedRuntimeFailure = { status: "shared_fixed_target_runtime_failure", reason: row.failure_reason };
    }
    if (!sharedRuntimeFailure && sourceIndex < sources.length - 1) {
      const completedInBatch = (sourceIndex + 1) % batchSize;
      const delay = completedInBatch === 0
        ? Number(options.batchCooldownMs)
        : Number(options.accountPacingMs);
      if (delay > 0) await sleep(delay);
    }
  }
  const retryRows = rows.filter(isTransientAccountFailure);
  if (!sharedRuntimeFailure && !riskSignal && retryRows.length) {
    const readiness = await options.riskCheck?.();
    const preRetryRisk = typeof readiness === "string"
      ? readiness
      : String(readiness?.riskSignal || "");
    const readinessFailure = typeof readiness === "object"
      ? String(readiness?.readinessFailure || "")
      : "";
    if (preRetryRisk) {
      riskSignal = preRetryRisk;
    } else if (readinessFailure) {
      for (const failed of retryRows) {
        failed.tail_retry_status = "not_attempted_browser_readiness_failure";
        failed.tail_retry_reason = readinessFailure;
        failed.extraction_diagnostics = {
          ...(failed.extraction_diagnostics || {}),
          tail_retry_status: readinessFailure,
        };
      }
    } else {
      await sleep(Math.max(0, options.tailRetryDelayMs));
    }
    if (riskSignal || readinessFailure) {
      return { rows, sharedRuntimeFailure, riskSignal };
    }
    for (const [retryIndex, failed] of retryRows.entries()) {
      const retryReadiness = await options.riskCheck?.();
      const retryRisk = typeof retryReadiness === "string"
        ? retryReadiness
        : String(retryReadiness?.riskSignal || "");
      const retryReadinessFailure = typeof retryReadiness === "object"
        ? String(retryReadiness?.readinessFailure || "")
        : "";
      if (retryRisk) {
        riskSignal = retryRisk;
        break;
      }
      if (retryReadinessFailure) {
        failed.tail_retry_status = "not_attempted_browser_readiness_failure";
        failed.tail_retry_reason = retryReadinessFailure;
        failed.extraction_diagnostics = {
          ...(failed.extraction_diagnostics || {}),
          tail_retry_status: retryReadinessFailure,
        };
        continue;
      }
      const source = sources.find((item) => String(item.account_name || item.name || "") === failed.account_name);
      const retried = await probe(client, source, options);
      retried.source_id = String(source.id || source.source_id || "");
      retried.attempts = 2;
      retried.first_attempt_failure = failed.extraction_diagnostics?.failure_code || failed.failure_reason;
      rows[rows.indexOf(failed)] = retried;
      riskSignal = sourceGlobalRisk(retried, rows.filter((item) => item !== retried));
      await options.onCheckpoint?.(rows, riskSignal);
      if (riskSignal) break;
      if (retried.shared_runtime_failure) {
        sharedRuntimeFailure = { status: "shared_fixed_target_runtime_failure", reason: retried.failure_reason };
        break;
      }
      if (retryIndex < retryRows.length - 1 && Number(options.accountPacingMs) > 0) {
        await sleep(Number(options.accountPacingMs));
      }
    }
  }
  return { rows, sharedRuntimeFailure, riskSignal };
}

export function runDouyinPreflight(root = ROOT, runner = spawnSync) {
  const result = runner("python3", [path.join(root, "scripts/check_douyin_session.py"), "--port", "9333"], {
    cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    const payload = JSON.parse(String(result.stdout || "{}"));
    return {
      ok: result.status === 0 && payload.ok === true && payload.login_state === "logged_in",
      status: String(payload.status || "login_preflight_failed"),
      login_state: String(payload.login_state || "indeterminate"),
      profile_identity: "fixed_douyin_profile_9333",
    };
  } catch {
    return {
      ok: false,
      status: "login_preflight_failed",
      login_state: "indeterminate",
      profile_identity: "fixed_douyin_profile_9333",
    };
  }
}

export function launchFixedDouyinBrowser(root = ROOT, runner = spawnSync) {
  const result = runner("python3", [
    path.join(root, "scripts/start_douyin_cdp_chrome.py"),
    "--port", "9333",
    "--mode", "hidden",
  ], { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  try {
    const payload = JSON.parse(String(result.stdout || "{}"));
    return {
      ok: result.status === 0 && payload.ok === true,
      status: String(payload.status || "fixed_browser_launch_failed"),
      mode: String(payload.mode || ""),
      cdp: String(payload.cdp || ""),
    };
  } catch {
    return { ok: false, status: "fixed_browser_launch_failed", mode: "", cdp: "" };
  }
}

export function explicitVerificationState(preflight) {
  return ["verification_required", "challenge_detected", "sms_verification_required"]
    .includes(String(preflight?.login_state || preflight?.status || ""));
}

export function reloadableIndeterminate(preflight) {
  return String(preflight?.login_state || "") === "indeterminate"
    && ["browser_readiness_inconclusive", "login_preflight_failed"]
      .includes(String(preflight?.status || ""));
}

export async function runDouyinPreflightWithRecheck(
  run = runDouyinPreflight,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  delayMs = 1200,
  reload = async () => {},
) {
  const startedAt = Date.now();
  const first = run();
  const attempts = [first];
  if (!first.ok && reloadableIndeterminate(first)) {
    await reload();
    await sleep(Math.max(0, delayMs));
    attempts.push(run());
  }
  const current = attempts.at(-1);
  return {
    ...current,
    status: current.ok
      ? "session_verified"
      : explicitVerificationState(current)
        ? "verification_required"
        : reloadableIndeterminate(current)
          ? "browser_readiness_inconclusive"
          : String(current.status || "browser_session_unavailable"),
    preflight_attempts: attempts.length,
    rechecked: attempts.length > 1,
    elapsed_ms: Math.max(0, Date.now() - startedAt),
  };
}

export async function runDouyinPreflightWithAutostart(
  run = runDouyinPreflight,
  launch = launchFixedDouyinBrowser,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  delayMs = 1200,
  reload = async () => {},
) {
  let first = run();
  let launcherCalls = 0;
  let launcherStatus = "not_required";
  if (!first.ok && String(first.status || "") === "not_running") {
    launcherCalls = 1;
    const launched = launch();
    launcherStatus = String(launched.status || "fixed_browser_launch_failed");
    if (
      !launched.ok
      || launched.mode !== "hidden"
      || launched.cdp !== "http://127.0.0.1:9333"
    ) {
      return {
        ok: false,
        status: launcherStatus === "profile_identity_mismatch"
          ? "profile_identity_mismatch"
          : "fixed_browser_launch_failed",
        login_state: "indeterminate",
        profile_identity: "fixed_douyin_profile_9333",
        preflight_attempts: 1,
        rechecked: false,
        launcher_calls: launcherCalls,
        launcher_status: launcherStatus,
      };
    }
    first = run();
  }
  let pending = first;
  const checked = await runDouyinPreflightWithRecheck(
    () => {
      const current = pending;
      pending = null;
      return current || run();
    },
    sleep,
    delayMs,
    reload,
  );
  return {
    ...checked,
    launcher_calls: launcherCalls,
    launcher_status: launcherStatus,
  };
}

export async function tailRetryReadinessCheck(
  run = runDouyinPreflight,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  delayMs = 1200,
  reload = async () => {},
  launch = launchFixedDouyinBrowser,
) {
  const current = await runDouyinPreflightWithAutostart(
    run, launch, sleep, delayMs, reload,
  );
  if (current.ok) return { riskSignal: "", readinessFailure: "", preflight: current };
  if (explicitVerificationState(current)) {
    return {
      riskSignal: String(current.login_state || current.status || "verification_required"),
      readinessFailure: "",
      preflight: current,
    };
  }
  return {
    riskSignal: "",
    readinessFailure: String(current.status || "browser_readiness_failed"),
    preflight: current,
  };
}

export function notifyManualVerification(runner = spawnSync) {
  const script = 'display notification "请在既有固定抖音页面完成滑块或短信验证，然后回到来源管理点击我已完成验证。" with title "抖音采集已暂停"';
  const result = runner("osascript", ["-e", script], { encoding: "utf8", stdio: "ignore" });
  return result.status === 0 ? "sent" : "failed";
}

export function checkpointPayload(sources, rows, riskSignal = "", priorRows = []) {
  const byId = new Map(rows.map((row) => [String(row.source_id || ""), row]));
  const priorById = new Map(priorRows.map((row) => [String(row.source_id || ""), row]));
  return sources.map((source, ordinal) => {
    const sourceId = String(source.id || source.source_id || "");
    const row = byId.get(sourceId);
    const prior = priorById.get(sourceId);
    if (prior && ["completed", "updated_no_new_items"].includes(String(prior.status || ""))) {
      return {
        source_id: sourceId,
        status: String(prior.status),
        artifact_sha256: String(prior.artifact_sha256 || ""),
        artifact_count: Number(prior.artifact_count) || 0,
        ordinal: Number(prior.ordinal) || 0,
      };
    }
    if (!row && prior) {
      return {
        source_id: sourceId,
        status: String(prior.status || "pending"),
        artifact_sha256: String(prior.artifact_sha256 || ""),
        artifact_count: Number(prior.artifact_count) || 0,
        ordinal: Number(prior.ordinal) || 0,
      };
    }
    let status = "pending";
    if (row?.status === "success") status = "completed";
    else if (row?.status === "updated_no_new_items") status = "updated_no_new_items";
    else if (row?.status === "not_attempted_waiting_manual_verification" || (!row && riskSignal)) {
      status = "not_attempted_waiting_manual_verification";
    } else if (row) status = "failed_account_local";
    const artifact = {
      video_ids: row?.video_ids || [],
      video_links: row?.video_links || [],
    };
    return {
      source_id: sourceId,
      status,
      artifact_sha256: ["completed", "updated_no_new_items"].includes(status)
        ? createHash("sha256").update(JSON.stringify(artifact)).digest("hex")
        : "",
      artifact_count: Array.isArray(row?.video_links) ? row.video_links.length : 0,
      ordinal,
    };
  });
}

export function hasUsableFinalSourceArtifact(outDir, runId) {
  const resultPath = path.join(outDir, "cdp_probe_results.json");
  const manualPath = path.join(outDir, "content_items_manual.jsonl");
  try {
    const result = JSON.parse(fs.readFileSync(resultPath, "utf8"));
    const artifact = result.manual_artifact || {};
    const bytes = fs.readFileSync(manualPath);
    const rows = bytes.toString("utf8").split("\n").filter((line) => line.trim()).length;
    return String(result.run_id || "") === String(runId || "")
      && ["completed", "completed_with_failures"].includes(String(result.status || ""))
      && String(artifact.run_id || "") === String(runId || "")
      && fs.realpathSync(manualPath) === String(artifact.path || "")
      && createHash("sha256").update(bytes).digest("hex") === String(artifact.sha256 || "")
      && bytes.length === Number(artifact.size)
      && rows > 0
      && rows === Number(artifact.row_count);
  } catch {
    return false;
  }
}

export function rehydrationSourceIds(priorRows, finalArtifactUsable) {
  if (finalArtifactUsable) return [];
  return priorRows
    .filter((row) => String(row.status || "") === "completed" && Number(row.artifact_count) > 0)
    .map((row) => String(row.source_id || ""))
    .filter(Boolean);
}

export function persistRiskCheckpoint(options, runId, sources, rows, riskSignal, notificationStatus = "") {
  const checkpointPath = path.join(options.outDir, "douyin_checkpoint_rows.json");
  const payload = checkpointPayload(sources, rows, riskSignal, options.priorCheckpointRows || []);
  fs.writeFileSync(checkpointPath, JSON.stringify(payload, null, 2), "utf8");
  const finalStatus = riskSignal
    ? "waiting_manual_verification"
    : (payload.every((row) => !["pending", "not_attempted_waiting_manual_verification"].includes(row.status))
      ? "completed"
      : "running");
  const result = spawnSync("python3", [
    path.join(ROOT, "scripts/source_control_cli.py"),
    "--db", options.sourceDb,
    "douyin-checkpoint",
    "--run-id", runId,
    "--profile-identity", "fixed_douyin_profile_9333",
    "--rows-json", checkpointPath,
    "--risk-status", finalStatus,
    "--risk-reason", riskSignal,
    "--preflight-state", riskSignal ? "verification_required" : "session_verified",
    "--notification-status", notificationStatus,
  ], { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  if (result.status !== 0) throw new Error("douyin_checkpoint_write_failed");
  return JSON.parse(String(result.stdout || "{}"));
}

export function deriveAccountHealth(events, source, runId) {
  const ordered = [...events].sort((a, b) => String(a.run_id).localeCompare(String(b.run_id)));
  const current = ordered.findLast((event) => event.run_id === runId) || ordered.at(-1) || {};
  let consecutiveFailures = 0;
  for (const event of [...ordered].reverse()) {
    if (["success", "updated_no_new_items"].includes(event.outcome)) break;
    consecutiveFailures += 1;
  }
  const window = ordered.slice(-10);
  const successes = window.filter((event) => ["success", "updated_no_new_items"].includes(event.outcome)).length;
  const configurationFailure = String(current.failure_class || "").startsWith("configuration_");
  return {
    source_id: source.source_id || `douyin:${createHash("sha256").update(`${source.account_name || source.name}|${source.url || ""}`).digest("hex").slice(0, 16)}`,
    account_name: source.account_name || source.name || "",
    platform: "douyin",
    configured_identity: configuredAccountIdentity(source.url || source.homepage_url || ""),
    verified_identity: current.verified_identity || "",
    enabled: source.default_enabled !== false && source.participates_main_sampling !== false,
    priority: source.source_role || "",
    last_attempt: current.attempted_at || "",
    last_success: [...ordered].reverse().find((event) => ["success", "updated_no_new_items"].includes(event.outcome))?.attempted_at || "",
    current_outcome: current.outcome || "not_attempted",
    failure_class: current.failure_class || "",
    consecutive_failures: consecutiveFailures,
    rolling_success: window.length >= 3 ? { successes, attempts: window.length, rate: successes / window.length } : null,
    action_required: configurationFailure || consecutiveFailures >= 3,
    recovery: configurationFailure
      ? "修复并验证 exact Douyin 身份后，下一次成功运行自动清零。"
      : "下一次 success/updated_no_new_items 自动清零 consecutive_failures。",
  };
}

export function atomicWriteJson(pathname, payload, io = fs) {
  const target = path.resolve(pathname);
  const parent = path.dirname(target);
  io.mkdirSync(parent, { recursive: true });
  const token = `${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}`;
  const temporary = `${target}.${token}.tmp`;
  const restore = `${target}.${token}.restore`;
  const original = io.existsSync(target) ? io.readFileSync(target) : null;
  let fd;
  let replaced = false;
  try {
    fd = io.openSync(temporary, "wx", 0o600);
    io.writeSync(fd, `${JSON.stringify(payload, null, 2)}\n`, null, "utf8");
    io.fsyncSync(fd);
    io.closeSync(fd);
    fd = undefined;
    io.renameSync(temporary, target);
    replaced = true;
    const dirFd = io.openSync(parent, "r");
    try {
      io.fsyncSync(dirFd);
    } finally {
      io.closeSync(dirFd);
    }
  } catch (error) {
    if (fd !== undefined) {
      try { io.closeSync(fd); } catch {}
    }
    if (replaced) {
      try {
        if (original === null) {
          io.unlinkSync(target);
        } else {
          const restoreFd = fs.openSync(restore, "wx", 0o600);
          try {
            fs.writeSync(restoreFd, original);
            fs.fsyncSync(restoreFd);
          } finally {
            fs.closeSync(restoreFd);
          }
          fs.renameSync(restore, target);
        }
      } catch (rollbackError) {
        error.rollback_error = String(rollbackError?.message || rollbackError);
      }
    }
    throw error;
  } finally {
    for (const candidate of [temporary, restore]) {
      try {
        if (fs.existsSync(candidate)) fs.unlinkSync(candidate);
      } catch {}
    }
  }
}

export function validateHealthProjection(projection, ledgerPath, durableBytes, runId) {
  const digest = createHash("sha256").update(durableBytes).digest("hex");
  const authority = projection?.authority || {};
  const eventKeys = Object.keys(JSON.parse(durableBytes).events || {})
    .filter((key) => key.startsWith(`${runId}|`))
    .sort();
  return {
    ok: projection?.schema_version === 1
      && projection?.projection_kind === "douyin_account_health_run_projection"
      && projection?.run_id === runId
      && authority.path === path.resolve(ledgerPath)
      && authority.schema_version === 1
      && authority.sha256 === digest
      && JSON.stringify(projection.event_keys || []) === JSON.stringify(eventKeys),
    authority_sha256: digest,
    event_keys: eventKeys,
  };
}

export function buildHealthProjection(ledgerPath, durableBytes, runId) {
  const durable = JSON.parse(durableBytes);
  if (durable?.schema_version !== 1 || !durable.events || typeof durable.events !== "object") {
    throw new Error("douyin_account_health_authority_readback_invalid");
  }
  const eventKeys = Object.keys(durable.events)
    .filter((key) => key.startsWith(`${runId}|`))
    .sort();
  const projection = {
    schema_version: 1,
    projection_kind: "douyin_account_health_run_projection",
    run_id: runId,
    authority: {
      path: path.resolve(ledgerPath),
      schema_version: durable.schema_version,
      sha256: createHash("sha256").update(durableBytes).digest("hex"),
    },
    event_keys: eventKeys,
    events: Object.fromEntries(eventKeys.map((key) => [key, durable.events[key]])),
    accounts: durable.accounts || [],
  };
  const validation = validateHealthProjection(projection, ledgerPath, durableBytes, runId);
  if (!validation.ok) throw new Error("douyin_account_health_projection_invalid");
  return projection;
}

export function persistAccountHealth(ledgerPath, runPath, sources, rows, runId, now = new Date().toISOString(), io = fs) {
  const existing = fs.existsSync(ledgerPath) ? JSON.parse(fs.readFileSync(ledgerPath, "utf8")) : { schema_version: 1, events: {} };
  existing.events ||= {};
  const sourceByName = new Map(sources.map((source) => [String(source.account_name || source.name || ""), source]));
  for (const row of rows) {
    const source = sourceByName.get(String(row.account_name || "")) || {
      account_name: row.account_name,
      url: row.homepage_url,
      default_enabled: true,
      participates_main_sampling: true,
    };
    const identity = configuredAccountIdentity(source.url || source.homepage_url || "");
    const sourceId = source.source_id || `douyin:${createHash("sha256").update(`${source.account_name || source.name}|${source.url || ""}`).digest("hex").slice(0, 16)}`;
    const failureCode = String(row.extraction_diagnostics?.failure_code || row.failure_code || "");
    const failureClass = ["success", "updated_no_new_items"].includes(row.status)
      ? ""
      : (failureCode.includes("configured_account") ? `configuration_${failureCode}` : (isTransientAccountFailure(row) ? "transient_timeout" : "account_failure"));
    existing.events[`${runId}|${sourceId}`] = {
      run_id: runId,
      source_id: sourceId,
      attempted_at: now,
      outcome: row.status,
      failure_class: failureClass,
      verified_identity: ["success", "updated_no_new_items"].includes(row.status) ? identity : "",
    };
  }
  const eventRows = Object.values(existing.events);
  const accounts = sources.map((source) => {
    const sourceId = source.source_id || `douyin:${createHash("sha256").update(`${source.account_name || source.name}|${source.url || ""}`).digest("hex").slice(0, 16)}`;
    return deriveAccountHealth(eventRows.filter((event) => event.source_id === sourceId), source, runId);
  });
  existing.updated_at = now;
  existing.accounts = accounts;
  atomicWriteJson(ledgerPath, existing, io);
  const durableBytes = fs.readFileSync(ledgerPath);
  const committed = JSON.parse(durableBytes);
  const expectedKeys = rows.map((row) => {
    const source = sourceByName.get(String(row.account_name || "")) || {
      account_name: row.account_name,
      url: row.homepage_url,
    };
    const sourceId = source.source_id || `douyin:${createHash("sha256").update(`${source.account_name || source.name}|${source.url || ""}`).digest("hex").slice(0, 16)}`;
    return `${runId}|${sourceId}`;
  });
  if (committed?.schema_version !== 1 || expectedKeys.some((key) => !committed.events?.[key])) {
    throw new Error("douyin_account_health_authority_readback_failed");
  }
  const authority = {
    ok: true,
    status: "committed",
    path: path.resolve(ledgerPath),
    schema_version: committed.schema_version,
    sha256: createHash("sha256").update(durableBytes).digest("hex"),
    event_keys: expectedKeys.sort(),
  };
  const projectionPayload = buildHealthProjection(ledgerPath, durableBytes, runId);
  let projection;
  try {
    atomicWriteJson(runPath, projectionPayload, io);
    const projectionReadback = JSON.parse(fs.readFileSync(runPath, "utf8"));
    const validation = validateHealthProjection(projectionReadback, ledgerPath, durableBytes, runId);
    if (!validation.ok) throw new Error("douyin_account_health_projection_readback_failed");
    projection = {
      ok: true,
      status: "committed",
      path: path.resolve(runPath),
      authority_sha256: authority.sha256,
      event_keys: validation.event_keys,
    };
  } catch (error) {
    projection = {
      ok: false,
      status: "health_projection_write_failed",
      path: path.resolve(runPath),
      authority_sha256: authority.sha256,
      event_keys: projectionPayload.event_keys,
      failure_type: String(error?.message || "health_projection_write_failed").split(":", 1)[0],
    };
  }
  return { accounts, authority, projection };
}

export function collectionStatusWithHealth(coverageOk, sharedRuntimeFailure, accountHealth) {
  if (sharedRuntimeFailure) return "source_runtime_failed";
  return coverageOk && accountHealth?.projection?.ok ? "completed" : "completed_with_failures";
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status} ${url}`);
  return response.json();
}

class CdpClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.seq = 0;
    this.pending = new Map();
    this.listeners = new Map();
  }

  async open() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("CDP websocket timeout")), 10000);
      this.ws.addEventListener("open", () => {
        clearTimeout(timeout);
        resolve();
      }, { once: true });
      this.ws.addEventListener("error", (event) => {
        clearTimeout(timeout);
        reject(new Error(`CDP websocket error: ${event.message || "unknown"}`));
      }, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      if (payload.id && this.pending.has(payload.id)) {
        const { resolve, reject } = this.pending.get(payload.id);
        this.pending.delete(payload.id);
        if (payload.error) reject(new Error(payload.error.message || JSON.stringify(payload.error)));
        else resolve(payload.result);
      } else if (payload.method) {
        for (const listener of this.listeners.get(payload.method) || []) listener(payload.params || {});
      }
    });
    this.ws.addEventListener("close", () => {
      const error = new Error("cdp_page_websocket_closed");
      error.code = "cdp_page_websocket_closed";
      for (const pending of this.pending.values()) pending.reject(error);
      this.pending.clear();
    });
  }

  on(method, listener) {
    if (!this.listeners.has(method)) this.listeners.set(method, new Set());
    this.listeners.get(method).add(listener);
    return () => this.listeners.get(method)?.delete(listener);
  }

  send(method, params = {}) {
    const id = ++this.seq;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP command timeout: ${method}`));
        }
      }, 20000);
    });
  }

  close() {
    try {
      this.ws?.close();
    } catch {
      // ignore close failures
    }
  }
}

export function isAttachmentTransitionError(error) {
  const message = String(error?.message || error || "").toLowerCase();
  return [
    "not attached to an active page",
    "cdp_page_websocket_closed",
    "inspected target navigated or closed",
    "target closed",
    "session closed",
  ].some((marker) => message.includes(marker));
}

function runtimeEvaluationError(code, recoverable = false) {
  const error = new Error(code);
  error.code = code;
  error.recoverable_context_transition = recoverable;
  return error;
}

export function decodeRuntimeEvaluation(evaluated, stage = "works_snapshot") {
  if (!evaluated || typeof evaluated !== "object" || evaluated.error) {
    throw runtimeEvaluationError(`${stage}_cdp_error`);
  }
  if (evaluated.exceptionDetails) {
    const description = String(
      evaluated.exceptionDetails?.exception?.description
        || evaluated.exceptionDetails?.text
        || "",
    ).toLowerCase();
    const contextTransition = [
      "execution context was destroyed",
      "cannot find context",
      "cannot find default execution context",
      "not attached to an active page",
      "inspected target navigated or closed",
    ].some((marker) => description.includes(marker));
    throw runtimeEvaluationError(
      contextTransition ? `${stage}_execution_context_transition` : `${stage}_javascript_exception`,
      contextTransition,
    );
  }
  if (!evaluated.result || !Object.prototype.hasOwnProperty.call(evaluated.result, "value")) {
    throw runtimeEvaluationError(`${stage}_value_missing`, true);
  }
  if (typeof evaluated.result.value !== "string") {
    throw runtimeEvaluationError(`${stage}_value_type_invalid`);
  }
  try {
    const decoded = JSON.parse(evaluated.result.value);
    if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) {
      throw new Error("invalid snapshot shape");
    }
    return decoded;
  } catch {
    throw runtimeEvaluationError(`${stage}_json_malformed`);
  }
}

export function accountWorksSnapshotExpression() {
  return `(() => {
    const worksSelector = '[data-e2e*="user-post"], [data-e2e*="user-work"], [data-e2e*="works"], [class*="user-post"], [class*="work-list"], [class*="post-list"]';
    const excludedSelector = '[data-e2e*="recommend"], [data-e2e*="search"], [data-e2e*="hot"], [class*="recommend"], [class*="search"], [class*="hot-list"], [class*="hotList"]';
    const roots = Array.from(document.querySelectorAll(worksSelector));
    const anchors = [];
    const seen = new Set();
    for (const root of roots) {
      if (root.closest(excludedSelector)) continue;
      for (const anchor of root.querySelectorAll('a[href*="/video/"], a[href*="modal_id="]')) {
        const href = anchor.href || '';
        const id = (href.match(/(?:\\/video\\/|modal_id=)(\\d{10,})/) || [])[1] || '';
        if (!id || seen.has(id) || anchor.closest(excludedSelector)) continue;
        seen.add(id);
        let text = anchor.innerText || anchor.getAttribute('aria-label') || anchor.title || '';
        let node = anchor;
        for (let index = 0; index < 4 && node && text.length < 20; index += 1) {
          node = node.parentElement;
          if (node && node.innerText) text = node.innerText;
        }
        const pinnedText = String(text || '');
        const pinned = Boolean(anchor.closest('[data-e2e*="pinned"], [class*="pinned"]'))
          || pinnedText.split(String.fromCharCode(10)).some((line) => line.trim() === '置顶');
        anchors.push({
          href,
          id,
          text: pinnedText.slice(0, 1000),
          in_works_grid: true,
          pinned,
          account_identity_match: true,
        });
      }
    }
    const bodyText = document.body ? document.body.innerText : '';
    return JSON.stringify({
      title: document.title || '',
      url: location.href || '',
      works_ready: roots.length > 0,
      works_root_count: roots.length,
      videoAnchors: anchors,
      text: bodyText.slice(0, 5000),
      loginHint: /登录|验证码|验证|captcha|verify/i.test(bodyText),
    });
  })()`;
}

export class FixedPageSession {
  constructor(cdp, target, options = {}) {
    this.cdp = cdp.replace(/\/$/, "");
    this.targetId = String(target.id || "");
    this.target = target;
    this.listTargets = options.listTargets || getJson;
    this.clientFactory = options.clientFactory || ((url) => new CdpClient(url));
    this.maxReattachments = Number.isInteger(options.maxReattachments) ? options.maxReattachments : 2;
    this.reattachments = 0;
    this.navigationTimeoutRecoveries = 0;
    this.attachmentRecoveryStreak = 0;
    this.capture = null;
    if (!this.targetId || !target.webSocketDebuggerUrl) throw new Error("fixed_douyin_target_identity_missing");
  }

  async open() {
    this.client = this.clientFactory(this.target.webSocketDebuggerUrl);
    await this.client.open();
    try {
      await this.client.send("Runtime.enable");
      await this.client.send("Page.enable");
      await this.client.send("Network.enable");
      this.installNetworkListeners();
    } catch (error) {
      if (!isAttachmentTransitionError(error)) throw error;
      await this.reattach();
    }
  }

  installNetworkListeners() {
    if (typeof this.client.on !== "function") return;
    this.client.on("Network.requestWillBeSent", (params) => {
      if (!this.capture) return;
      const candidate = classifyWorksResponse(
        params.request?.url,
        "XHR",
        this.capture.accountIdentity,
        params.request?.method,
      );
      if (candidate.accepted) this.capture.requests.set(params.requestId, candidate);
    });
    this.client.on("Network.responseReceived", (params) => {
      if (!this.capture) return;
      const candidate = this.capture.requests.get(params.requestId);
      if (!candidate) return;
      if (String(params.type || "").toUpperCase() !== "XHR") {
        this.capture.requests.delete(params.requestId);
        return;
      }
      candidate.response_received = true;
    });
    this.client.on("Network.loadingFinished", async ({ requestId }) => {
      const candidate = this.capture?.requests.get(requestId);
      if (!candidate) return;
      this.capture.requests.delete(requestId);
      try {
        const body = await this.client.send("Network.getResponseBody", { requestId });
        this.capture.results.push(parseWorksResponseBody(body?.body, this.capture.accountIdentity));
      } catch (error) {
        this.capture.results.push({ ok: false, failure_code: "douyin_works_response_body_missing", detail: error.message });
      }
    });
  }

  beginWorksCapture(accountIdentity) {
    this.capture = { accountIdentity, requests: new Map(), results: [] };
  }

  takeWorksCaptureResults() {
    return this.capture?.results.splice(0) || [];
  }

  async reattach() {
    if (this.attachmentRecoveryStreak >= this.maxReattachments) {
      const error = new Error("fixed_target_attachment_recovery_exhausted");
      error.code = "fixed_target_attachment_recovery_exhausted";
      throw error;
    }
    const targets = await this.listTargets(`${this.cdp}/json/list`);
    const current = targets.find((item) => String(item.id || "") === this.targetId);
    if (!current || current.type !== "page" || !current.webSocketDebuggerUrl) {
      const error = new Error("fixed_target_attachment_lost");
      error.code = "fixed_target_attachment_lost";
      throw error;
    }
    this.client?.close();
    this.target = current;
    this.reattachments += 1;
    this.attachmentRecoveryStreak += 1;
    await this.open();
  }

  async recoverExecutionContext() {
    await this.reattach();
  }

  async send(method, params = {}) {
    try {
      const result = await this.client.send(method, params);
      this.attachmentRecoveryStreak = 0;
      return result;
    } catch (error) {
      if (method === "Page.navigate" && isNavigationTimeout(error)) {
        if (this.navigationTimeoutRecoveries >= 1) {
          const exhausted = new Error("douyin_navigation_timeout_after_reattach");
          exhausted.code = "douyin_navigation_timeout_after_reattach";
          throw exhausted;
        }
        this.navigationTimeoutRecoveries += 1;
        await this.reattach();
        try {
          const result = await this.client.send(method, params);
          this.attachmentRecoveryStreak = 0;
          return result;
        } catch (retryError) {
          if (isNavigationTimeout(retryError)) {
            const exhausted = new Error("douyin_navigation_timeout_after_reattach");
            exhausted.code = "douyin_navigation_timeout_after_reattach";
            throw exhausted;
          }
          throw retryError;
        }
      }
      if (!isAttachmentTransitionError(error)) throw error;
      await this.reattach();
      const result = await this.client.send(method, params);
      this.attachmentRecoveryStreak = 0;
      return result;
    }
  }

  close() {
    this.client?.close();
  }
}

export function isNavigationTimeout(error) {
  const text = String(error?.message || error || "");
  return /Page\.navigate.*timed?\s*out|timed?\s*out.*Page\.navigate|navigation.*timed?\s*out/i.test(text);
}

export async function fixedDouyinTarget(cdp, listTargets = getJson) {
  const targets = await listTargets(`${cdp.replace(/\/$/, "")}/json/list`);
  const pages = targets.filter((item) => item.type === "page"
    && /^https?:\/\/([^/]+\.)?douyin\.com\//i.test(item.url || "")
    && item.webSocketDebuggerUrl);
  if (!pages.length) {
    const error = new Error("fixed_douyin_target_missing");
    error.code = "fixed_douyin_target_missing";
    throw error;
  }
  return pages[0];
}

export function expectedAccountUrl(actual, expected) {
  try {
    const current = new URL(actual);
    const target = new URL(expected);
    return current.hostname.endsWith("douyin.com")
      && current.pathname.replace(/\/$/, "") === target.pathname.replace(/\/$/, "");
  } catch {
    return false;
  }
}

export function configuredAccountIdentity(homepage) {
  try {
    const parsed = new URL(homepage);
    return decodeURIComponent((parsed.pathname.match(/\/user\/([^/?#]+)/) || [])[1] || "");
  } catch {
    return "";
  }
}

export function classifyWorksResponse(rawUrl, resourceType, expectedAccountIdentity, method = "GET") {
  try {
    const parsed = new URL(rawUrl);
    const accountIdentity = parsed.searchParams.get("sec_user_id") || "";
    const accepted = parsed.pathname === "/aweme/v1/web/aweme/post/"
      && String(resourceType || "").toUpperCase() === "XHR"
      && String(method || "").toUpperCase() === "GET"
      && Boolean(expectedAccountIdentity)
      && accountIdentity === expectedAccountIdentity;
    return {
      accepted,
      method: String(method || "").toUpperCase(),
      resource_type: String(resourceType || ""),
      path_pattern: parsed.pathname,
      exact_account_bound: accountIdentity === expectedAccountIdentity && Boolean(accountIdentity),
      query_keys: [...parsed.searchParams.keys()].sort(),
    };
  } catch {
    return { accepted: false };
  }
}

function networkWorkCard(item, expectedAccountIdentity) {
  const id = String(item?.aweme_id || "");
  const authorIdentity = String(item?.author?.sec_uid || item?.author?.sec_user_id || "");
  if (!/^\d{10,}$/.test(id) || authorIdentity !== expectedAccountIdentity) return null;
  const statistics = item?.statistics && typeof item.statistics === "object"
    ? item.statistics : {};
  const fact = (key) => {
    if (!Object.prototype.hasOwnProperty.call(statistics, key)) {
      return { value: null, missing_reason: "field_not_returned" };
    }
    const value = Number(statistics[key]);
    if (!Number.isFinite(value) || value < 0) {
      return { value: null, missing_reason: "field_invalid" };
    }
    return { value, missing_reason: "" };
  };
  const published = Number(item?.create_time);
  const publishedValid = Number.isInteger(published) && published > 0;
  const likes = fact("digg_count");
  const comments = fact("comment_count");
  const favorites = fact("collect_count");
  const shares = fact("share_count");
  const missing = Object.fromEntries([
    ["published_at", publishedValid ? "" : (item?.create_time == null ? "field_not_returned" : "field_invalid")],
    ["likes", likes.missing_reason],
    ["comments", comments.missing_reason],
    ["favorites", favorites.missing_reason],
    ["shares", shares.missing_reason],
  ].filter(([, reason]) => reason));
  return {
    id,
    video_id: id,
    href: `https://www.douyin.com/video/${id}`,
    url: `https://www.douyin.com/video/${id}`,
    text: String(item?.desc || "").slice(0, 1000),
    pinned: Boolean(item?.is_top === 1 || item?.is_top === true || item?.is_pinned === 1),
    create_time: publishedValid ? published : null,
    likes: likes.value,
    comments: comments.value,
    favorites: favorites.value,
    shares: shares.value,
    fact_missing_reasons: missing,
    fact_provenance: {
      capture: "configured_account_page_owned_works_response",
      endpoint: "/aweme/v1/web/aweme/post/",
      response_fields: {
        published_at: "create_time",
        likes: "statistics.digg_count",
        comments: "statistics.comment_count",
        favorites: "statistics.collect_count",
        shares: "statistics.share_count",
      },
    },
    in_works_grid: true,
    account_identity_match: true,
  };
}

export function parseWorksResponseBody(rawBody, expectedAccountIdentity) {
  if (typeof rawBody !== "string" || !rawBody.trim()) {
    return { ok: false, failure_code: "douyin_works_response_body_missing", cards: [] };
  }
  let payload;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return { ok: false, failure_code: "douyin_works_response_json_malformed", cards: [] };
  }
  if (!Array.isArray(payload?.aweme_list)) {
    return { ok: false, failure_code: "douyin_works_response_schema_mismatch", cards: [] };
  }
  const cards = payload.aweme_list.map((item) => networkWorkCard(item, expectedAccountIdentity)).filter(Boolean);
  const rejectedItemCount = payload.aweme_list.length - cards.length;
  if (payload.aweme_list.length && cards.length === 0) {
    return { ok: false, failure_code: "douyin_works_response_account_or_item_mismatch", cards: [] };
  }
  return { ok: true, cards, response_item_count: payload.aweme_list.length, rejected_item_count: rejectedItemCount };
}

export function worksInteractionExpression() {
  return `(() => {
    const candidates = Array.from(document.querySelectorAll('[role="tab"], [data-e2e*="user-post"], [data-e2e*="works"]'));
    const works = candidates.find((node) => /作品/.test(node.innerText || node.getAttribute('aria-label') || ''));
    if (works && typeof works.click === 'function') works.click();
    window.scrollBy(0, Math.max(500, Math.floor(window.innerHeight * 0.8)));
    return JSON.stringify({ clicked: Boolean(works), works_surface_count: candidates.length });
  })()`;
}

export async function waitForPageOwnedWorks(client, options = {}) {
  const timeoutMs = Math.max(1000, Number(options.timeoutMs) || 15000);
  const pollMs = Math.max(10, Number(options.pollMs) || 250);
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const now = options.now || (() => Date.now());
  const started = now();
  let firstFailure = null;
  do {
    for (const result of client.takeWorksCaptureResults()) {
      if (result.ok) return result;
      firstFailure ||= result;
    }
    await client.send("Runtime.evaluate", { expression: worksInteractionExpression(), returnByValue: true });
    await sleep(pollMs);
  } while (now() - started < timeoutMs);
  const code = firstFailure?.failure_code || "douyin_works_response_timeout";
  const error = new Error(code);
  error.code = code;
  throw error;
}

export async function waitForNavigationAndWorksGrid(client, expectedUrl, options = {}) {
  const timeoutMs = Math.max(1000, Number(options.timeoutMs) || 15000);
  const pollMs = Math.max(10, Number(options.pollMs) || 250);
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const now = options.now || (() => Date.now());
  const started = now();
  const maxContextRecoveries = Number.isInteger(options.maxContextRecoveries)
    ? options.maxContextRecoveries
    : 2;
  let contextRecoveries = 0;
  let last = { title: "", url: "", text: "", works_ready: false };
  do {
    const history = await client.send("Page.getNavigationHistory");
    const committedEntry = (history.entries || [])[history.currentIndex];
    const navigationCommitted = expectedAccountUrl(committedEntry?.url || "", expectedUrl);
    try {
      const evaluated = await client.send("Runtime.evaluate", {
        expression: accountWorksSnapshotExpression(),
        returnByValue: true,
        awaitPromise: true,
      });
      last = decodeRuntimeEvaluation(evaluated, "works_snapshot");
    } catch (error) {
      if (!error.recoverable_context_transition
        || contextRecoveries >= maxContextRecoveries
        || typeof client.recoverExecutionContext !== "function") {
        throw error;
      }
      contextRecoveries += 1;
      await client.recoverExecutionContext();
      continue;
    }
    last.navigation_committed = navigationCommitted;
    last.context_recoveries = contextRecoveries;
    if (navigationCommitted && expectedAccountUrl(last.url, expectedUrl) && last.works_ready) return last;
    await sleep(pollMs);
  } while (now() - started < timeoutMs);
  const currentUrl = String(last.url || "").trim();
  const blank = !String(last.title || "").trim() && (!currentUrl || currentUrl === "about:blank")
    && !String(last.text || "").trim();
  const error = new Error(blank ? "shared_fixed_target_blank" : "works_grid_readiness_timeout");
  error.code = blank ? "shared_fixed_target_blank" : "works_grid_readiness_timeout";
  error.last_state = last;
  throw error;
}

function extractVideoLinksFromText(text, videoLimit) {
  const links = [];
  const ids = [];
  for (const match of text.matchAll(/(?:\/video\/|modal_id=)(\d{10,})/g)) {
    const id = match[1];
    if (!ids.includes(id)) ids.push(id);
  }
  for (const id of ids.slice(0, videoLimit)) {
    links.push(`https://www.douyin.com/video/${id}`);
  }
  return { ids: ids.slice(0, videoLimit), links };
}

export function videoIdFromUrl(value) {
  return (String(value || "").match(/(?:\/video\/|modal_id=)(\d{10,})/) || [])[1] || "";
}

export function isContaminatedWorkCard(card) {
  const href = String(card?.href || card?.url || "");
  const text = String(card?.text || "");
  if (!card?.in_works_grid || !videoIdFromUrl(href)) return true;
  if (/baiduspider|\/search(?:\/|\?|$)|hotspot|hot\/search|goods|product/i.test(href)) return true;
  return /教材|食品|商品|热搜聚合|广告/.test(text) && !card.account_identity_match;
}

export function selectIncrementalWorks(cards, seenIds = [], { scanLimit = 10, videoLimit = 3 } = {}) {
  const seen = new Set([...seenIds].map(String));
  const scanned = cards.slice(0, Math.max(10, Number(scanLimit) || 10));
  const selected = [];
  const counters = { cards_scanned: scanned.length, new: 0, seen: 0, pinned: 0, contaminated: 0, rejected: 0 };
  for (const card of scanned) {
    const id = videoIdFromUrl(card.href || card.url);
    if (isContaminatedWorkCard(card)) {
      counters.contaminated += 1;
      continue;
    }
    if (!id) {
      counters.rejected += 1;
      continue;
    }
    if (card.pinned) {
      counters.pinned += 1;
      continue;
    }
    if (seen.has(id)) {
      counters.seen += 1;
      continue;
    }
    if (!selected.some((item) => videoIdFromUrl(item.href || item.url) === id)) selected.push(card);
    if (selected.length >= videoLimit) break;
  }
  counters.new = selected.length;
  return { selected, counters, status: selected.length ? "updated_with_new_items" : "updated_no_new_items" };
}

export function loadSeenVideoIds(ledgerPath, runsRoot = path.join(ROOT, "output/runs")) {
  const ids = new Set();
  if (ledgerPath && fs.existsSync(ledgerPath)) {
    const payload = JSON.parse(fs.readFileSync(ledgerPath, "utf8"));
    for (const value of payload.video_ids || []) ids.add(String(value));
  }
  if (fs.existsSync(runsRoot)) {
    for (const run of fs.readdirSync(runsRoot)) {
      const csv = path.join(runsRoot, run, "content_items.csv");
      if (!fs.existsSync(csv)) continue;
      for (const match of fs.readFileSync(csv, "utf8").matchAll(/(?:\/video\/|modal_id=)(\d{10,})/g)) ids.add(match[1]);
    }
  }
  return ids;
}

export function writeSeenVideoIds(ledgerPath, seenIds, runId) {
  const target = path.resolve(ledgerPath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify({
    schema_version: 1,
    last_run_id: runId,
    video_ids: [...new Set([...seenIds].map(String))].sort(),
  }, null, 2), { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temporary, target);
}

function atomicJson(pathname, payload) {
  atomicWriteJson(pathname, payload);
}

export function loadCandidateLifecycle(ledgerPath) {
  if (!ledgerPath || !fs.existsSync(ledgerPath)) return { schema_version: 1, items: {} };
  const payload = JSON.parse(fs.readFileSync(ledgerPath, "utf8"));
  if (payload?.schema_version !== 1 || !payload.items || typeof payload.items !== "object" || Array.isArray(payload.items)) {
    throw new Error("douyin_lifecycle_malformed");
  }
  return payload;
}

export function materializeHistoricalBacklog(lifecycle) {
  const items = [];
  const failures = [];
  for (const entry of Object.values(lifecycle.items || {})) {
    if (entry?.state !== "collected_unreviewed") continue;
    try {
      const artifactPath = String(entry.artifact_path || "");
      if (!artifactPath || !fs.existsSync(artifactPath)) throw new Error("artifact_missing");
      const bytes = fs.readFileSync(artifactPath);
      const sha = createHash("sha256").update(bytes).digest("hex");
      if (sha !== entry.artifact_sha256) throw new Error("artifact_hash_mismatch");
      const artifact = JSON.parse(bytes.toString("utf8"));
      if (artifact["内容指纹"] !== entry.fingerprint) throw new Error("artifact_identity_mismatch");
      items.push({
        ...artifact,
        "候选时态": "historical_unreviewed",
        "首次发现批次": entry.first_seen_run_id,
        "首次发现日期": entry.first_seen_date,
        "是否今日新增": "否",
      });
    } catch (error) {
      failures.push({ fingerprint: String(entry?.fingerprint || ""), reason: error.message });
    }
  }
  return { items, failures };
}

export function persistCollectedCandidates(ledgerPath, lifecycle, items, runId) {
  const artifactDir = path.join(path.dirname(ledgerPath), "douyin_candidate_artifacts");
  const runDate = String(runId).slice(4, 12).replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3");
  for (const item of items) {
    const fingerprintValue = String(item["内容指纹"] || "");
    if (!fingerprintValue) continue;
    const existing = lifecycle.items[fingerprintValue];
    if (existing && ["reviewed", "written_04", "generated_06"].includes(existing.state)) continue;
    const artifactPath = path.join(artifactDir, `${fingerprintValue}.json`);
    atomicJson(artifactPath, item);
    const bytes = fs.readFileSync(artifactPath);
    lifecycle.items[fingerprintValue] = {
      schema_version: 1,
      fingerprint: fingerprintValue,
      video_id: videoIdFromUrl(item["内容链接"]),
      url: String(item["内容链接"] || ""),
      source_type: String(item["来源类型"] || ""),
      account: String(item["账号名/公众号名"] || ""),
      title: String(item["内容标题"] || ""),
      first_seen_run_id: existing?.first_seen_run_id || runId,
      first_seen_date: existing?.first_seen_date || runDate,
      state: "collected_unreviewed",
      artifact_path: path.resolve(artifactPath),
      artifact_sha256: createHash("sha256").update(bytes).digest("hex"),
    };
  }
  atomicJson(ledgerPath, lifecycle);
  return lifecycle;
}

export function mergeNewAndBacklog(newItems, backlogItems, runId) {
  const dateValue = String(runId).slice(4, 12).replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3");
  const newFingerprints = new Set(newItems.map((item) => item["内容指纹"]));
  return [
    ...newItems.map((item) => ({
      ...item, "候选时态": "today_new", "首次发现批次": runId,
      "首次发现日期": dateValue, "是否今日新增": "是",
    })),
    ...backlogItems.filter((item) => !newFingerprints.has(item["内容指纹"])),
  ];
}

function fingerprint(input) {
  let hash = 5381;
  for (let i = 0; i < input.length; i += 1) {
    hash = ((hash << 5) + hash) + input.charCodeAt(i);
    hash &= 0xffffffff;
  }
  return `douyin_cdp_${(hash >>> 0).toString(16)}`;
}

function normalizeCardText(text) {
  const lines = String(text || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !["置顶", "热点", "共创", "广告"].includes(line))
    .filter((line) => !/^\d+(?:\.\d+)?万?$/.test(line));
  return lines.join(" ")
    .replace(/\s+/g, " ")
    .replace(/打开看看|去看看|查看详情/g, "")
    .trim();
}

export function buildHomepageCardContentItem(row, link, index) {
  const cards = row.video_cards || [];
  const card = cards.find((item) => item.href === link || item.url === link || link.endsWith(String(item.video_id || ""))) || {};
  const title = normalizeCardText(card.text || "");
  const body = title || `${row.account_name || "抖音对标账号"}主页发现作品：${link}`;
  const createTime = Number(card.create_time);
  const publishedAt = Number.isInteger(createTime) && createTime > 0
    ? new Date(createTime * 1000).toISOString() : "";
  return {
    "来源类型": "对标视频",
    "平台": "抖音",
    "账号名/公众号名": row.account_name || "",
    "内容标题": title || `${row.account_name || "抖音"}主页作品 ${index + 1}`,
    "内容链接": link,
    "内容形态": "short_video_homepage_card",
    "封面文字": "",
    "正文/字幕/简介片段": body,
    "发布时间": publishedAt,
    "评论区问题": "",
    "截图/OCR文本": "",
    "抓取方式": "douyin_cdp_homepage_card",
    "抓取状态": "success",
    "失败原因": "",
    "内容指纹": fingerprint(`${row.account_name || ""}|${link}|${body}`),
    "正文原始长度": body.length,
    "正文是否截断": "否",
    "解析说明": "从登录态主页作品区提取标题/文案卡片；未做口播转写、评论抓取或视频理解。适合标题先筛选，人工确认后再转写。",
    "source_url": link,
    "aweme_id": String(card.video_id || ""),
    "published_at": publishedAt,
    "likes": card.likes ?? null,
    "comments": card.comments ?? null,
    "favorites": card.favorites ?? null,
    "shares": card.shares ?? null,
    "fact_missing_reasons": card.fact_missing_reasons || {},
    "fact_provenance": card.fact_provenance || {
      capture: "configured_account_collection",
    },
  };
}

export function buildHomepageCardItems(rows) {
  const items = [];
  for (const row of rows) {
    if (row.status !== "success") continue;
    for (const [index, link] of (row.video_links || []).entries()) {
      items.push(buildHomepageCardContentItem(row, link, index));
    }
  }
  return items;
}

export function buildWorksFactParity(rows, contentItems) {
  const contentByUrl = new Map(contentItems.map((row) => [String(row.source_url || row["内容链接"] || ""), row]));
  const fields = [
    ["published_at", "create_time"],
    ["likes", "likes"],
    ["comments", "comments"],
    ["favorites", "favorites"],
    ["shares", "shares"],
  ];
  const items = [];
  let supportedFields = 0;
  let projectionMissing = 0;
  let realZeroFields = 0;
  for (const row of rows) {
    for (const card of row.video_cards || []) {
      const sourceUrl = String(card.url || card.href || "");
      const content = contentByUrl.get(sourceUrl) || {};
      const candidate = {
        candidate_id: `douyin:${card.video_id}`,
        source_url: sourceUrl,
        published_at: content.published_at || "",
        likes: content.likes ?? null,
        comments: content.comments ?? null,
        favorites: content.favorites ?? null,
        shares: content.shares ?? null,
        fact_missing_reasons: content.fact_missing_reasons || {},
        fact_provenance: content.fact_provenance || {},
      };
      const comparisons = {};
      for (const [target, rawKey] of fields) {
        const rawSupported = rawKey === "create_time"
          ? Number.isInteger(card.create_time) && card.create_time > 0
          : card[rawKey] !== null && card[rawKey] !== undefined;
        let expected = card[rawKey];
        if (rawKey === "create_time" && rawSupported) {
          expected = new Date(card.create_time * 1000).toISOString();
        }
        if (rawSupported) {
          supportedFields += 1;
          if (expected === 0) realZeroFields += 1;
        }
        const contentValue = content[target] ?? null;
        const candidateValue = candidate[target] ?? null;
        const equal = !rawSupported || (contentValue === expected && candidateValue === expected);
        if (rawSupported && !equal) projectionMissing += 1;
        comparisons[target] = {
          raw_supported: rawSupported,
          raw_value: rawSupported ? expected : null,
          content_value: contentValue,
          candidate_value: candidateValue,
          parity: equal,
          missing_reason: rawSupported ? "" : String(card.fact_missing_reasons?.[target] || "field_not_returned"),
        };
      }
      items.push({
        aweme_id: String(card.video_id || ""),
        source_url: sourceUrl,
        provenance: card.fact_provenance || {},
        comparisons,
      });
    }
  }
  const eligibleComplete = items.filter((row) =>
    ["published_at", "likes", "comments", "favorites", "shares"].every(
      (field) => row.comparisons[field].raw_supported && row.comparisons[field].parity,
    ));
  return {
    status: projectionMissing === 0 ? "passed" : "failed",
    ordinary_work_count: items.length,
    raw_supported_field_count: supportedFields,
    projection_missing_count: projectionMissing,
    parity_percent: supportedFields ? ((supportedFields - projectionMissing) * 100 / supportedFields) : null,
    real_zero_field_count: realZeroFields,
    complete_fact_candidate_count: eligibleComplete.length,
    items,
  };
}

export function validateContentItemLineage(rows, items) {
  const allowedByAccount = new Map(
    rows
      .filter((row) => ["success", "updated_no_new_items"].includes(row.status))
      .map((row) => [String(row.account_name || ""), new Set(row.video_links || [])]),
  );
  const violations = [];
  for (const [index, item] of items.entries()) {
    const account = String(item["账号名/公众号名"] || "");
    const link = String(item["内容链接"] || "");
    if (!allowedByAccount.has(account) || !allowedByAccount.get(account).has(link)) {
      violations.push({ index, account_name: account, content_url: link });
    }
  }
  return { ok: violations.length === 0, violation_count: violations.length, violations };
}

export function buildCoverage(sources, rows) {
  const plan = validateSourcePlan(sources);
  const plannedNames = plan.account_names;
  const plannedSet = new Set(plannedNames);
  const rowNames = rows.map((row) => String(row.account_name || "").trim());
  const rowCounts = new Map();
  for (const name of rowNames) rowCounts.set(name, (rowCounts.get(name) || 0) + 1);
  const duplicateRows = [...rowCounts.entries()].filter(([, count]) => count > 1).map(([name]) => name);
  const unknownRows = rowNames.filter((name) => !plannedSet.has(name));
  const missingRows = plannedNames.filter((name) => !rowCounts.has(name));
  const perAccount = {};
  const failedAccounts = [];
  let successfulAccounts = 0;
  for (const row of rows) {
    const name = String(row.account_name || "").trim();
    const artifactCount = Array.isArray(row.video_links) ? row.video_links.length : 0;
    perAccount[name] = artifactCount;
    if ((row.status === "success" && artifactCount > 0) || row.status === "updated_no_new_items") {
      successfulAccounts += 1;
    } else {
      failedAccounts.push({
        account_name: name,
        status: row.status === "success" ? "zero_artifact" : (row.status || "failed"),
        failure_reason: row.status === "success" && artifactCount === 0
          ? "Account probe returned success without a source artifact."
          : (row.failure_reason || "Account probe failed without a reason."),
        artifact_count: artifactCount,
      });
    }
  }
  const attempted = rows.length;
  const structuralOk = plan.ok && !duplicateRows.length && !unknownRows.length && !missingRows.length;
  const invariantOk = attempted === plannedNames.length
    && successfulAccounts + failedAccounts.length === attempted;
  return {
    ok: structuralOk && invariantOk && failedAccounts.length === 0,
    account_limit: 0,
    planned_accounts: plannedNames.length,
    planned_account_names: plannedNames,
    attempted_accounts: attempted,
    successful_accounts: successfulAccounts,
    failed_account_count: failedAccounts.length,
    failed_accounts: failedAccounts,
    per_account_artifact_counts: perAccount,
    missing_account_rows: missingRows,
    duplicate_account_rows: duplicateRows,
    unknown_account_rows: unknownRows,
    plan_validation: plan,
    invariants: {
      attempted_equals_planned: attempted === plannedNames.length,
      success_plus_failed_equals_attempted: successfulAccounts + failedAccounts.length === attempted,
      account_lineage_unique_and_complete: structuralOk,
    },
  };
}

export function buildSourceRuntimeCoverage(sources, rows, failure) {
  const plan = validateSourcePlan(sources);
  const attemptedNames = new Set(rows.map((row) => String(row.account_name || "")));
  return {
    ok: false,
    account_limit: 0,
    planned_accounts: plan.planned_accounts,
    planned_account_names: plan.account_names,
    attempted_accounts: rows.filter((row) => row.status !== "not_attempted_source_runtime_failure").length,
    successful_accounts: 0,
    failed_account_count: 0,
    failed_accounts: [],
    source_runtime_failure_count: 1,
    source_runtime_failure: failure,
    artifact_count: 0,
    not_attempted_account_names: plan.account_names.filter((name) => !attemptedNames.has(name)
      || rows.some((row) => row.account_name === name && row.status === "not_attempted_source_runtime_failure")),
    per_account_artifact_counts: Object.fromEntries(plan.account_names.map((name) => [name, 0])),
    missing_account_rows: [],
    duplicate_account_rows: [],
    unknown_account_rows: [],
    plan_validation: plan,
    invariants: {
      attempted_equals_planned: false,
      success_plus_failed_equals_attempted: false,
      account_lineage_unique_and_complete: true,
      failed_source_artifacts_zero: true,
    },
  };
}

export async function probeAccount(client, source, options) {
  const startedAt = Date.now();
  const homepage = source.url || source.homepage_url || "";
  if (!homepage) {
    return {
      account_name: source.account_name || source.name || "",
      homepage_url: "",
      status: "needs_url",
      failure_reason: "配置中缺少抖音主页链接",
      video_ids: [],
      video_links: [],
    };
  }
  try {
    const accountIdentity = configuredAccountIdentity(homepage);
    if (!accountIdentity) {
      const error = new Error("douyin_configured_account_identity_missing");
      error.code = "douyin_configured_account_identity_missing";
      throw error;
    }
    if (typeof client.beginWorksCapture !== "function" || typeof client.takeWorksCaptureResults !== "function") {
      const error = new Error("douyin_page_network_capture_unavailable");
      error.code = "douyin_page_network_capture_unavailable";
      throw error;
    }
    client.beginWorksCapture(accountIdentity);
    const navigation = await client.send("Page.navigate", { url: homepage });
    if (navigation.errorText) throw new Error(`navigation_failed:${navigation.errorText}`);
    const payload = await waitForNavigationAndWorksGrid(client, homepage, { timeoutMs: options.waitMs });
    if (
      payload.loginHint
      || /验证码|安全验证|短信验证|滑块|captcha|verification|challenge/i.test(
        `${payload.text || ""} ${payload.title || ""} ${payload.url || ""}`,
      )
    ) {
      const error = new Error("verification_required");
      error.code = "verification_required";
      throw error;
    }
    const worksLoaded = Boolean(payload.works_ready);
    const accountWorksFailed = /服务异常|重新刷新拉取数据/.test(payload.text || "") || !worksLoaded;
    if (accountWorksFailed) {
      const error = new Error("douyin_works_surface_unavailable");
      error.code = "douyin_works_surface_unavailable";
      throw error;
    }
    const pageOwned = await waitForPageOwnedWorks(client, { timeoutMs: options.waitMs });
    const incremental = selectIncrementalWorks(pageOwned.cards, options.seenVideoIds || new Set(), options);
    const trustedWorks = !accountWorksFailed && incremental.status === "updated_with_new_items";
    const noNew = !accountWorksFailed && incremental.status === "updated_no_new_items";
    const status = trustedWorks ? "success" : (noNew ? "updated_no_new_items" : (payload.loginHint ? "needs_login_or_verification" : "partial_untrusted"));
    const failure = trustedWorks
      ? ""
      : (
          accountWorksFailed
            ? "主页作品区未可信加载，发现的视频 ID 可能来自热门推荐或页脚，不作为账号最近作品。"
            : (noNew ? "" : (payload.loginHint ? "页面疑似需要登录/验证后才能看到作品链接" : "页面已渲染但未发现可信作品 ID，可能仍是 JS 壳或作品列表懒加载"))
        );
    const selectedCards = trustedWorks ? incremental.selected : [];
    const selectedIds = selectedCards.map((item) => videoIdFromUrl(item.href || item.url));
    return {
      account_name: source.account_name || source.name || "",
      homepage_url: homepage,
      source_role: source.source_role || "",
      column: source.column || "",
      status,
      failure_reason: failure,
      page_title: payload.title || "",
      current_url: payload.url || "",
      video_ids: selectedIds,
      video_links: selectedIds.map((id) => `https://www.douyin.com/video/${id}`),
      video_cards: selectedCards.map((item) => ({
        video_id: item.id,
        href: item.href,
        url: `https://www.douyin.com/video/${item.id}`,
        text: item.text || "",
        pinned: Boolean(item.pinned),
        create_time: item.create_time,
        likes: item.likes,
        comments: item.comments,
        favorites: item.favorites,
        shares: item.shares,
        fact_missing_reasons: item.fact_missing_reasons || {},
        fact_provenance: item.fact_provenance || {},
      })),
      discovery_counters: incremental.counters,
      extraction_diagnostics: {
        works_root_count: Number(payload.works_root_count || 0),
        card_count: pageOwned.cards.length,
        context_recoveries: Number(payload.context_recoveries || 0),
        extraction_source: "page_owned_exact_account_xhr",
        endpoint_path: "/aweme/v1/web/aweme/post/",
        method: "GET",
        resource_type: "XHR",
        exact_account_bound: true,
        response_item_count: pageOwned.response_item_count,
        rejected_item_count: pageOwned.rejected_item_count,
        phase_timing_ms: { total: Math.max(0, Date.now() - startedAt) },
        fixed_target_reattachments: Number(client.reattachments || 0),
        navigation_timeout_recoveries: Number(client.navigationTimeoutRecoveries || 0),
      },
      freshness_state: incremental.status,
      untrusted_video_ids: [],
      untrusted_video_links: [],
      text_preview: payload.text || "",
      works_preview: "",
      boundary: "低频只读；不导出cookie/token/profile；不抓评论；不下载视频。",
    };
  } catch (error) {
    return {
      account_name: source.account_name || source.name || "",
      homepage_url: homepage,
      source_role: source.source_role || "",
      column: source.column || "",
      status: "failed",
      failure_reason: error.message,
      source_global_risk: [
        "verification_required", "logged_out", "challenge_detected", "sms_verification_required",
      ].includes(String(error.code || "")) ? String(error.code) : "",
      shared_runtime_failure: String(error.code || "").startsWith("works_snapshot_") || [
        "shared_fixed_target_blank",
        "fixed_douyin_target_missing",
        "fixed_target_attachment_lost",
        "fixed_target_attachment_recovery_exhausted",
        "works_snapshot_execution_context_transition",
        "works_snapshot_value_missing",
        "douyin_page_network_capture_unavailable",
      ].includes(error.code),
      extraction_diagnostics: {
        failure_code: String(error.code || "account_probe_failed"),
        extraction_source: "page_owned_exact_account_xhr",
        phase_timing_ms: { total: Math.max(0, Date.now() - startedAt) },
        fixed_target_reattachments: Number(client.reattachments || 0),
        navigation_timeout_recoveries: Number(client.navigationTimeoutRecoveries || 0),
      },
      video_ids: [],
      video_links: [],
    };
  }
}

async function main() {
  const accountGate = validateFullAccountLimitArgs(process.argv.slice(2));
  if (!accountGate.ok) {
    console.log(JSON.stringify(limitedPlanRejection(accountGate)));
    return 2;
  }
  const options = parseArgs();
  options.accountLimit = accountGate.value;
  options.scanLimit = Math.max(10, Number(options.scanLimit) || 10);
  options.seenVideoIds = loadSeenVideoIds(options.seenLedger);
  const lifecycle = loadCandidateLifecycle(options.lifecycleLedger);
  for (const entry of Object.values(lifecycle.items)) {
    if (entry?.video_id) options.seenVideoIds.add(String(entry.video_id));
  }
  const runId = String(process.env.AI_ACCOUNT_RADAR_RUN_ID || process.env.RUN_ID || "").trim();
  if (!options.checkOnly && !/^run_\d{8}_\d{6}(?:_[A-Za-z0-9_-]+)?$/.test(runId)) {
    console.log(JSON.stringify({ ok: false, status: "run_identity_missing", collection_started: false, writes_feishu: false }));
    return 2;
  }
  fs.mkdirSync(options.outDir, { recursive: true });

  const configuredSources = selectedSources(loadSources(options.config));
  const plan = validateSourcePlan(configuredSources);
  if (!plan.ok) {
    const failure = {
      ok: false,
      status: "invalid_account_plan",
      check_only: options.checkOnly,
      writes_feishu: false,
      collection_started: false,
      coverage: plan,
    };
    fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(failure, null, 2), "utf8");
    console.log(JSON.stringify(failure, null, 2));
    return 2;
  }
  if (options.checkOnly) {
    const preview = {
      ok: true,
      status: "planned",
      check_only: true,
      writes_feishu: false,
      collection_started: false,
      cdp_contacted: false,
      coverage: {
        account_limit: options.accountLimit,
        planned_accounts: plan.planned_accounts,
        planned_account_names: plan.account_names,
        executable_accounts: plan.executable_accounts,
        invalid_accounts: plan.invalid_accounts,
        pacing: {
          batch_size: options.batchSize,
          account_interval_ms: options.accountPacingMs,
          batch_cooldown_ms: options.batchCooldownMs,
          timeout_tail_retry_limit: 1,
          timeout_tail_retry_cooldown_ms: options.tailRetryDelayMs,
        },
      },
    };
    fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(preview, null, 2), "utf8");
    console.log(JSON.stringify(preview, null, 2));
    return 0;
  }

  let fixedTarget;
  let pageClient;
  const fixedSession = async () => {
    if (pageClient) return pageClient;
    fixedTarget = await fixedDouyinTarget(options.cdp);
    pageClient = new FixedPageSession(options.cdp, fixedTarget);
    await pageClient.open();
    return pageClient;
  };
  const reloadFixedSession = async () => {
    const session = await fixedSession();
    await session.send("Page.reload", { ignoreCache: false });
  };
  const preflight = await runDouyinPreflightWithAutostart(
    runDouyinPreflight,
    launchFixedDouyinBrowser,
    (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    1200,
    reloadFixedSession,
  );
  if (!preflight.ok) {
    const verificationRequired = explicitVerificationState(preflight);
    const notificationStatus = verificationRequired ? notifyManualVerification() : "not_required";
    const waitingRows = plan.valid_sources.map((source) => ({
      source_id: String(source.id || source.source_id || ""),
      account_name: source.account_name || source.name || "",
      status: verificationRequired
        ? "not_attempted_waiting_manual_verification"
        : "not_attempted_browser_readiness_failure",
      video_ids: [],
      video_links: [],
    }));
    if (verificationRequired) {
      persistRiskCheckpoint(
        options,
        runId,
        plan.valid_sources,
        waitingRows,
        preflight.login_state || preflight.status,
        notificationStatus,
      );
    }
    const failure = {
      ok: false,
      status: verificationRequired ? "waiting_manual_verification" : preflight.status,
      reason: verificationRequired
        ? (preflight.login_state || preflight.status)
        : preflight.status,
      preflight,
      account_navigations: 0,
      completed_accounts: 0,
      remaining_accounts: plan.valid_sources.length,
      notification_status: notificationStatus,
      collection_started: false,
      writes_feishu: false,
    };
    fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(failure, null, 2), "utf8");
    console.log(JSON.stringify(failure, null, 2));
    pageClient?.close();
    return verificationRequired ? 4 : 5;
  }

  let version;
  try {
    version = await getJson(`${options.cdp.replace(/\/$/, "")}/json/version`);
  } catch (error) {
    const failure = {
      ok: false,
      status: "cdp_unavailable",
      cdp: options.cdp,
      failure_reason: `无法连接 Chrome DevTools：${error.message}`,
      next_step: "先运行 python3 scripts/start_douyin_cdp_chrome.py --port 9333 --foreground 登录，再运行 python3 scripts/check_douyin_session.py --port 9333；不得改用其他 profile 或端口。",
    };
    fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(failure, null, 2), "utf8");
    console.log(JSON.stringify(failure, null, 2));
    pageClient?.close();
    return 2;
  }

  const sources = plan.valid_sources;
  const invalidRows = plan.invalid_accounts.map((account) => ({
    account_name: account.account_name,
    homepage_url: account.homepage_url,
    status: "invalid_configuration",
    failure_reason: account.failure_code,
    failure_code: account.failure_code,
    action_required: true,
    action: account.action,
    artifact_count: 0,
    video_ids: [],
    video_links: [],
    extraction_diagnostics: {
      failure_code: account.failure_code,
      extraction_source: "pre_browser_source_plan",
    },
  }));
  let sharedRuntimeFailure = null;
  let rows = invalidRows;
  try {
    await fixedSession();
  } catch (error) {
    sharedRuntimeFailure = { status: error.code || "fixed_douyin_target_missing", reason: error.message };
  }
  try {
    const prior = spawnSync("python3", [
      path.join(ROOT, "scripts/source_control_cli.py"),
      "--db", options.sourceDb,
      "douyin-risk",
      "--run-id", runId,
    ], { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    const priorRisk = prior.status === 0 ? JSON.parse(String(prior.stdout || "{}")) : { checkpoints: [] };
    options.priorCheckpointRows = priorRisk.checkpoints || [];
    const rehydrateIds = new Set(rehydrationSourceIds(
      options.priorCheckpointRows,
      hasUsableFinalSourceArtifact(options.outDir, runId),
    ));
    options.completedSourceIds = (priorRisk.checkpoints || [])
      .filter((row) => ["completed", "updated_no_new_items"].includes(row.status))
      .filter((row) => !rehydrateIds.has(String(row.source_id || "")))
      .map((row) => row.source_id);
    let notificationStatus = "";
    options.onCheckpoint = async (checkpointRows, riskSignal) => {
      if (riskSignal && !notificationStatus) notificationStatus = notifyManualVerification();
      persistRiskCheckpoint(
        options,
        runId,
        sources,
        checkpointRows,
        riskSignal,
        notificationStatus,
      );
    };
    options.riskCheck = async () => tailRetryReadinessCheck(
      runDouyinPreflight,
      (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      1200,
      reloadFixedSession,
      launchFixedDouyinBrowser,
    );
    const probed = await probeSourcesWithTailRetry(pageClient, sources, options);
    rows = [...invalidRows, ...probed.rows];
    sharedRuntimeFailure = probed.sharedRuntimeFailure;
    if (probed.riskSignal) {
      const completedCount = rows.filter((row) =>
        ["success", "updated_no_new_items", "failed"].includes(row.status)
      ).length;
      const riskResult = {
        ok: false,
        status: "waiting_manual_verification",
        reason: probed.riskSignal,
        completed_accounts: completedCount,
        remaining_accounts: sources.length - completedCount,
        notification_status: notificationStatus,
        rows,
      };
      fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(riskResult, null, 2), "utf8");
      console.log(JSON.stringify(riskResult, null, 2));
      return 4;
    }
  } finally {
    pageClient?.close();
  }
  for (const row of rows) {
    row.artifact_count = Array.isArray(row.video_links) ? row.video_links.length : 0;
    if (row.status === "success" && row.artifact_count === 0) {
      row.status = "zero_artifact";
      row.failure_reason = "Account probe returned success without a source artifact.";
    }
  }
  const videoLinks = Array.from(new Set(
    rows.filter((row) => row.status === "success").flatMap((row) => row.video_links || []),
  ));
  let resolverResult = {
    attempted: false,
    ok: false,
    jsonl: "",
    csv: "",
    stderr: "",
  };
  if (videoLinks.length && !options.worksFactsProof) {
    const jsonl = path.join(options.outDir, "content_items.jsonl");
    const csv = path.join(options.outDir, "content_items.csv");
    const rawDir = path.join(options.outDir, "raw_resolver");
    const args = [
      RESOLVER,
      "--out",
      jsonl,
      "--csv",
      csv,
      "--raw-dir",
      rawDir,
      "--dry-run",
    ];
    for (const link of videoLinks) {
      args.push("--url", link);
    }
    const proc = spawnSync("python3", args, {
      cwd: ROOT,
      encoding: "utf8",
      timeout: 60000,
    });
    resolverResult = {
      attempted: true,
      ok: proc.status === 0,
      jsonl,
      csv,
      stdout: proc.stdout?.slice(-4000) || "",
      stderr: proc.stderr?.slice(-4000) || "",
    };
  }

  const manualJsonl = path.join(options.outDir, "content_items_manual.jsonl");
  const newlyCollectedItems = buildHomepageCardItems(rows);
  const worksFactParity = buildWorksFactParity(rows, newlyCollectedItems);
  if (options.worksFactsProof) {
    const parityPath = path.join(options.outDir, "works_fact_parity.json");
    fs.writeFileSync(parityPath, JSON.stringify(worksFactParity, null, 2), "utf8");
    resolverResult.skipped_reason = "works_facts_proof";
    resolverResult.works_fact_parity = parityPath;
  }
  persistCollectedCandidates(options.lifecycleLedger, lifecycle, newlyCollectedItems, runId);
  const backlog = materializeHistoricalBacklog(lifecycle);
  const backlogEligibleAccounts = new Set(
    rows.filter((row) => ["success", "updated_no_new_items"].includes(row.status)).map((row) => String(row.account_name || "")),
  );
  backlog.items = backlog.items.filter((item) => backlogEligibleAccounts.has(String(item["账号名/公众号名"] || "")));
  const homepageCardItems = mergeNewAndBacklog(newlyCollectedItems, backlog.items, runId);
  for (const item of homepageCardItems) item["运行批次"] = runId;
  fs.writeFileSync(
    manualJsonl,
    homepageCardItems.map((item) => JSON.stringify(item)).join("\n") + (homepageCardItems.length ? "\n" : ""),
    "utf8",
  );
  resolverResult.manual_jsonl = manualJsonl;
  resolverResult.homepage_card_items = homepageCardItems.length;
  const manualStat = fs.statSync(manualJsonl);
  const manualArtifact = {
    run_id: runId,
    path: fs.realpathSync(manualJsonl),
    sha256: createHash("sha256").update(fs.readFileSync(manualJsonl)).digest("hex"),
    size: manualStat.size,
    mtime_ms: Math.trunc(manualStat.mtimeMs),
    row_count: homepageCardItems.length,
  };

  const coverage = sharedRuntimeFailure
    ? buildSourceRuntimeCoverage(configuredSources, rows, sharedRuntimeFailure)
    : buildCoverage(configuredSources, rows);
  for (const item of homepageCardItems) {
    const account = String(item["账号名/公众号名"] || "");
    if (Object.hasOwn(coverage.per_account_artifact_counts, account)) {
      coverage.per_account_artifact_counts[account] = (coverage.per_account_artifact_counts[account] || 0) +
        (item["候选时态"] === "historical_unreviewed" ? 1 : 0);
    }
  }
  coverage.account_limit = options.accountLimit;
  const backlogLinks = new Map();
  for (const item of backlog.items) {
    const account = String(item["账号名/公众号名"] || "");
    if (!backlogLinks.has(account)) backlogLinks.set(account, new Set());
    backlogLinks.get(account).add(String(item["内容链接"] || ""));
  }
  const itemLineage = validateContentItemLineage(rows.map((row) => ({
    ...row,
    video_links: [...(row.video_links || []), ...(backlogLinks.get(String(row.account_name || "")) || [])],
  })), homepageCardItems);
  if (!itemLineage.ok) coverage.ok = false;
  const sourcesByName = new Map(configuredSources.map((source) => [
    String(source.account_name || source.name || ""),
    source,
  ]));
  const healthEvents = rows.map((row) => {
    const source = sourcesByName.get(String(row.account_name || ""));
    return {
      source_id: String(source?.id || ""),
      attempted_at: new Date().toISOString(),
      outcome: String(row.status || "failed"),
      failure_class: String(row.failure_code || row.failure_reason || ""),
      artifact_count: Number(row.artifact_count || 0),
      verified_identity: String(source?.verified_identity || ""),
      substitute_count: 0,
    };
  });
  const healthInput = path.join(options.outDir, "account_health_events.json");
  fs.writeFileSync(healthInput, JSON.stringify(healthEvents, null, 2), "utf8");
  const healthProc = spawnSync("python3", [
    path.join(ROOT, "scripts/source_control_cli.py"),
    "--db", options.sourceDb,
    "record-events", "--run-id", runId, "--input", healthInput,
  ], { cwd: ROOT, encoding: "utf8" });
  const accountHealth = {
    ok: healthProc.status === 0,
    status: healthProc.status === 0 ? "committed" : "source_control_event_commit_failed",
    error: healthProc.status === 0 ? "" : String(healthProc.stderr || healthProc.stdout || "").slice(-1000),
  };

  const output = {
    ok: coverage.ok,
    status: coverage.ok && accountHealth.ok && !sharedRuntimeFailure ? "completed" : "completed_with_failures",
    check_only: false,
    writes_feishu: false,
    run_id: runId,
    cdp_browser: version.Browser || "",
    coverage,
    source_runtime_failure: sharedRuntimeFailure,
    fixed_target_id: fixedTarget?.id || "",
    fixed_target_reattachments: pageClient?.reattachments || 0,
    item_lineage: itemLineage,
    source_plan: plan,
    account_health: {
      ok: accountHealth.ok,
      status: accountHealth.status,
      authority: "source_control_sqlite",
      durable_path: path.resolve(options.sourceDb),
      event_count: healthEvents.length,
      error: accountHealth.error,
    },
    accounts: rows.length,
    discovered_video_links: videoLinks.length,
    resolver: resolverResult,
    manual_artifact: manualArtifact,
    candidate_lifecycle: {
      ledger_path: path.resolve(options.lifecycleLedger),
      today_new_count: newlyCollectedItems.length,
      historical_unreviewed_count: homepageCardItems.length - newlyCollectedItems.length,
      isolated_artifact_failures: backlog.failures,
    },
    rows,
    works_fact_parity: worksFactParity,
  };
  fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(output, null, 2), "utf8");
  const completedSeen = new Set(options.seenVideoIds);
  for (const id of rows.flatMap((row) => row.video_ids || [])) completedSeen.add(String(id));
  writeSeenVideoIds(options.seenLedger, completedSeen, runId);
  const csv = [
    ["account_name", "status", "homepage_url", "video_ids", "video_links", "failure_reason"].join(","),
    ...rows.map((row) => [
      row.account_name,
      row.status,
      row.homepage_url,
      JSON.stringify(row.video_ids || []),
      JSON.stringify(row.video_links || []),
      row.failure_reason || "",
    ].map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(",")),
  ].join("\n");
  fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.csv"), csv, "utf8");
  console.log(JSON.stringify(output, null, 2));
  return output.ok ? 0 : 3;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main()
    .then((code) => { process.exitCode = code; })
    .catch((error) => {
      console.error(error);
      process.exitCode = 1;
    });
}
