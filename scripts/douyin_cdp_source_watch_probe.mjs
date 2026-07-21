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
    retries: 2,
    checkOnly: false,
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
    else if (arg === "--retries") options.retries = Number(args[++i]);
    else if (arg === "--check-only") options.checkOnly = true;
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
    .filter((source) => source.platform === "抖音" && roles.has(source.source_role))
}

export function validateSourcePlan(sources) {
  const names = sources.map((source) => String(source.account_name || source.name || "").trim());
  const missing = names.map((name, index) => ({ name, index })).filter((item) => !item.name);
  const counts = new Map();
  for (const name of names) counts.set(name, (counts.get(name) || 0) + 1);
  const duplicates = [...counts.entries()].filter(([, count]) => count > 1).map(([name]) => name);
  return {
    ok: missing.length === 0 && duplicates.length === 0,
    planned_accounts: names.length,
    account_names: names,
    missing_account_name_indexes: missing.map((item) => item.index),
    duplicate_account_names: duplicates,
  };
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
      }
    });
    this.ws.addEventListener("close", () => {
      const error = new Error("cdp_page_websocket_closed");
      error.code = "cdp_page_websocket_closed";
      for (const pending of this.pending.values()) pending.reject(error);
      this.pending.clear();
    });
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

export class FixedPageSession {
  constructor(cdp, target, options = {}) {
    this.cdp = cdp.replace(/\/$/, "");
    this.targetId = String(target.id || "");
    this.target = target;
    this.listTargets = options.listTargets || getJson;
    this.clientFactory = options.clientFactory || ((url) => new CdpClient(url));
    this.maxReattachments = Number.isInteger(options.maxReattachments) ? options.maxReattachments : 2;
    this.reattachments = 0;
    if (!this.targetId || !target.webSocketDebuggerUrl) throw new Error("fixed_douyin_target_identity_missing");
  }

  async open() {
    this.client = this.clientFactory(this.target.webSocketDebuggerUrl);
    await this.client.open();
    try {
      await this.client.send("Runtime.enable");
      await this.client.send("Page.enable");
    } catch (error) {
      if (!isAttachmentTransitionError(error)) throw error;
      await this.reattach();
    }
  }

  async reattach() {
    if (this.reattachments >= this.maxReattachments) {
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
    await this.open();
  }

  async send(method, params = {}) {
    try {
      return await this.client.send(method, params);
    } catch (error) {
      if (!isAttachmentTransitionError(error)) throw error;
      await this.reattach();
      return this.client.send(method, params);
    }
  }

  close() {
    this.client?.close();
  }
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

export async function waitForNavigationAndWorksGrid(client, expectedUrl, options = {}) {
  const timeoutMs = Math.max(1000, Number(options.timeoutMs) || 15000);
  const pollMs = Math.max(10, Number(options.pollMs) || 250);
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const now = options.now || (() => Date.now());
  const started = now();
  let last = { title: "", url: "", body: "", works_ready: false };
  do {
    const history = await client.send("Page.getNavigationHistory");
    const committedEntry = (history.entries || [])[history.currentIndex];
    const navigationCommitted = expectedAccountUrl(committedEntry?.url || "", expectedUrl);
    const evaluated = await client.send("Runtime.evaluate", {
      expression: `(() => JSON.stringify({
        title: document.title || '',
        url: location.href || '',
        body: (document.body?.innerText || '').slice(0, 1000),
        works_ready: Boolean(document.querySelector('[data-e2e*="user-post"], [data-e2e*="user-work"], [data-e2e*="works"], [class*="user-post"], [class*="work-list"], [class*="post-list"]'))
      }))()`,
      returnByValue: true,
      awaitPromise: true,
    });
    last = JSON.parse(evaluated.result.value || "{}");
    last.navigation_committed = navigationCommitted;
    if (navigationCommitted && expectedAccountUrl(last.url, expectedUrl) && last.works_ready) return last;
    await sleep(pollMs);
  } while (now() - started < timeoutMs);
  const currentUrl = String(last.url || "").trim();
  const blank = !String(last.title || "").trim() && (!currentUrl || currentUrl === "about:blank")
    && !String(last.body || "").trim();
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
  fs.mkdirSync(path.dirname(pathname), { recursive: true });
  const temporary = `${pathname}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(payload, null, 2), { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temporary, pathname);
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
  return {
    "来源类型": "对标视频",
    "平台": "抖音",
    "账号名/公众号名": row.account_name || "",
    "内容标题": title || `${row.account_name || "抖音"}主页作品 ${index + 1}`,
    "内容链接": link,
    "内容形态": "short_video_homepage_card",
    "封面文字": "",
    "正文/字幕/简介片段": body,
    "发布时间": "",
    "评论区问题": "",
    "截图/OCR文本": "",
    "抓取方式": "douyin_cdp_homepage_card",
    "抓取状态": "success",
    "失败原因": "",
    "内容指纹": fingerprint(`${row.account_name || ""}|${link}|${body}`),
    "正文原始长度": body.length,
    "正文是否截断": "否",
    "是否来自已解析URL复用": "否",
    "解析说明": "从登录态主页作品区提取标题/文案卡片；未做口播转写、评论抓取或视频理解。适合标题先筛选，人工确认后再转写。",
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
    const navigation = await client.send("Page.navigate", { url: homepage });
    if (navigation.errorText) throw new Error(`navigation_failed:${navigation.errorText}`);
    await waitForNavigationAndWorksGrid(client, homepage, { timeoutMs: options.waitMs });
    const result = await client.send("Runtime.evaluate", {
      expression: `(() => {
        const videoAnchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="modal_id="]')).map(a => {
          const href = a.href || "";
          const id = (href.match(/(?:\\/video\\/|modal_id=)(\\d{10,})/) || [])[1] || "";
          let text = a.innerText || a.getAttribute("aria-label") || a.title || "";
          let node = a;
          for (let i = 0; i < 4 && node && text.length < 20; i += 1) {
            node = node.parentElement;
            if (node && node.innerText) text = node.innerText;
          }
          const cardRoot = a.closest('[data-e2e*="user-post"], [data-e2e*="user-work"], [data-e2e*="works"], [class*="user-post"], [class*="work-list"], [class*="post-list"]');
          const pinned = Boolean(a.closest('[data-e2e*="pinned"], [class*="pinned"]')) || /(^|\n)\s*置顶\s*(\n|$)/.test(text);
          const accountIdentityMatch = Boolean(cardRoot && !cardRoot.closest('[data-e2e*="recommend"], [class*="recommend"], [class*="search"]'));
          return { href, id, text: String(text || "").slice(0, 1000), in_works_grid: Boolean(cardRoot), pinned, account_identity_match: accountIdentityMatch };
        });
        const text = document.body ? document.body.innerText : '';
        const worksStart = text.indexOf('日期筛选');
        const worksEnd = worksStart >= 0 ? text.indexOf('广告投放', worksStart) : -1;
        const worksText = worksStart >= 0
          ? text.slice(worksStart, worksEnd > worksStart ? worksEnd : Math.min(text.length, worksStart + 4000))
          : '';
        return JSON.stringify({
          title: document.title,
          url: location.href,
          videoAnchors,
          text: text.slice(0, 5000),
          worksText: worksText.slice(0, 4000),
          loginHint: /登录|验证码|验证|captcha|verify/i.test(text)
        });
      })()`,
      returnByValue: true,
      awaitPromise: true,
    });
    const payload = JSON.parse(result.result.value || "{}");
    const worksText = payload.worksText || "";
    const worksLoaded = (payload.videoAnchors || []).some((item) => item.in_works_grid)
      || worksText.replace(/\s/g, "").length >= 80;
    const accountWorksFailed = /服务异常|重新刷新拉取数据/.test(payload.text || "") || !worksLoaded;
    const incremental = selectIncrementalWorks(payload.videoAnchors || [], options.seenVideoIds || new Set(), options);
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
      })),
      discovery_counters: incremental.counters,
      freshness_state: incremental.status,
      untrusted_video_ids: [],
      untrusted_video_links: [],
      text_preview: payload.text || "",
      works_preview: worksText,
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
      shared_runtime_failure: [
        "shared_fixed_target_blank",
        "fixed_douyin_target_missing",
        "fixed_target_attachment_lost",
        "fixed_target_attachment_recovery_exhausted",
      ].includes(error.code),
      video_ids: [],
      video_links: [],
    };
  }
}

async function probeAccountWithRetry(client, source, options) {
  let last = null;
  const attempts = Math.max(1, options.retries || 1);
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const result = await probeAccount(client, source, options);
    result.attempts = attempt;
    last = result;
    if (["success", "updated_no_new_items"].includes(result.status) || result.shared_runtime_failure) return result;
    if (attempt < attempts) {
      await new Promise((resolve) => setTimeout(resolve, Math.min(5000, 1000 * attempt)));
    }
  }
  return last;
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

  const sources = selectedSources(loadSources(options.config));
  const plan = validateSourcePlan(sources);
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
      },
    };
    fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(preview, null, 2), "utf8");
    console.log(JSON.stringify(preview, null, 2));
    return 0;
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
    return 2;
  }

  const rows = [];
  let sharedRuntimeFailure = null;
  let fixedTarget;
  try {
    fixedTarget = await fixedDouyinTarget(options.cdp);
  } catch (error) {
    sharedRuntimeFailure = { status: error.code || "fixed_douyin_target_missing", reason: error.message };
  }
  const pageClient = fixedTarget ? new FixedPageSession(options.cdp, fixedTarget) : null;
  if (pageClient) {
    await pageClient.open();
  }
  try {
    for (const source of sources) {
      if (sharedRuntimeFailure) {
        rows.push({
          account_name: source.account_name || source.name || "",
          homepage_url: source.url || source.homepage_url || "",
          status: "not_attempted_source_runtime_failure",
          failure_reason: sharedRuntimeFailure.reason,
          video_ids: [],
          video_links: [],
        });
        continue;
      }
      const row = await probeAccountWithRetry(pageClient, source, options);
      rows.push(row);
      if (row.shared_runtime_failure) {
        sharedRuntimeFailure = { status: "shared_fixed_target_runtime_failure", reason: row.failure_reason };
      }
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
  if (videoLinks.length) {
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
    ? buildSourceRuntimeCoverage(sources, rows, sharedRuntimeFailure)
    : buildCoverage(sources, rows);
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

  const output = {
    ok: coverage.ok,
    status: sharedRuntimeFailure ? "source_runtime_failed" : (coverage.ok ? "completed" : "completed_with_failures"),
    check_only: false,
    writes_feishu: false,
    run_id: runId,
    cdp_browser: version.Browser || "",
    coverage,
    source_runtime_failure: sharedRuntimeFailure,
    fixed_target_id: fixedTarget?.id || "",
    fixed_target_reattachments: pageClient?.reattachments || 0,
    item_lineage: itemLineage,
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
