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
    accountLimit: 0,
    videoLimit: 3,
    waitMs: 7000,
    retries: 2,
    onlyAccountNames: "",
    checkOnly: false,
  };
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--config") options.config = args[++i];
    else if (arg === "--out-dir") options.outDir = args[++i];
    else if (arg === "--cdp") options.cdp = args[++i];
    else if (arg === "--account-limit") options.accountLimit = Number(args[++i]);
    else if (arg === "--video-limit") options.videoLimit = Number(args[++i]);
    else if (arg === "--wait-ms") options.waitMs = Number(args[++i]);
    else if (arg === "--retries") options.retries = Number(args[++i]);
    else if (arg === "--only-account-names") options.onlyAccountNames = args[++i] || "";
    else if (arg === "--check-only") options.checkOnly = true;
  }
  return options;
}

export function loadSources(configPath) {
  const text = fs.readFileSync(configPath, "utf8");
  return JSON.parse(text).sources || [];
}

export function selectedSources(sources, limit) {
  const roles = new Set(["current_main_competitor", "current_aux_competitor"]);
  const only = new Set(String(limit.onlyAccountNames || "")
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean));
  const max = Number(limit.accountLimit || 0);
  const rows = sources
    .filter((source) => source.platform === "抖音" && roles.has(source.source_role))
    .filter((source) => !only.size || only.has(source.account_name || source.name || ""))
  return max > 0 ? rows.slice(0, max) : rows;
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

async function waitForTargetWebSocket(cdp, targetId) {
  const base = cdp.replace(/\/$/, "");
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const targets = await getJson(`${base}/json/list`);
    const target = targets.find((item) => item.id === targetId);
    if (target?.webSocketDebuggerUrl) return target;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Cannot find background Chrome target websocket: ${targetId}`);
}

async function createBackgroundTab(cdp, browserClient) {
  const result = await browserClient.send("Target.createTarget", {
    url: "about:blank",
    background: true,
  });
  const targetId = result.targetId;
  if (!targetId) throw new Error(`Chrome did not return targetId: ${JSON.stringify(result)}`);
  const target = await waitForTargetWebSocket(cdp, targetId);
  await minimizeTargetWindow(browserClient, targetId);
  return target;
}

async function minimizeTargetWindow(browserClient, targetId) {
  try {
    const result = await browserClient.send("Browser.getWindowForTarget", { targetId });
    if (result.windowId) {
      await browserClient.send("Browser.setWindowBounds", {
        windowId: result.windowId,
        bounds: { windowState: "minimized" },
      });
    }
  } catch {
    // Some Chrome targets do not expose a window. Background target creation is
    // still the main focus-avoidance mechanism, so minimizing is best-effort.
  }
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
      .filter((row) => row.status === "success")
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
    if (row.status === "success" && artifactCount > 0) {
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

async function probeAccount(cdp, browserClient, source, options) {
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
  let tab;
  let client;
  try {
    tab = await createBackgroundTab(cdp, browserClient);
    client = new CdpClient(tab.webSocketDebuggerUrl);
    await client.open();
    await client.send("Runtime.enable");
    await client.send("Page.enable");
    await client.send("Page.navigate", { url: homepage });
    await minimizeTargetWindow(browserClient, tab.id);
    await new Promise((resolve) => setTimeout(resolve, options.waitMs));
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
          return { href, id, text: String(text || "").slice(0, 1000) };
        });
        const anchors = Array.from(document.querySelectorAll('a[href]')).map(a => a.href).join('\\n');
        const html = document.documentElement ? document.documentElement.outerHTML : '';
        const text = document.body ? document.body.innerText : '';
        const worksStart = text.indexOf('日期筛选');
        const worksEnd = worksStart >= 0 ? text.indexOf('广告投放', worksStart) : -1;
        const worksText = worksStart >= 0
          ? text.slice(worksStart, worksEnd > worksStart ? worksEnd : Math.min(text.length, worksStart + 4000))
          : '';
        return JSON.stringify({
          title: document.title,
          url: location.href,
          anchors,
          videoAnchors,
          text: text.slice(0, 5000),
          worksText: worksText.slice(0, 4000),
          htmlSnippet: html.slice(0, 50000),
          loginHint: /登录|验证码|验证|captcha|verify/i.test(text + html)
        });
      })()`,
      returnByValue: true,
      awaitPromise: true,
    });
    const payload = JSON.parse(result.result.value || "{}");
    const combined = `${payload.anchors || ""}\n${payload.htmlSnippet || ""}`;
    const extracted = extractVideoLinksFromText(combined, options.videoLimit);
    const worksText = payload.worksText || "";
    const worksLoaded = worksText.replace(/\s/g, "").length >= 80;
    const accountWorksFailed = /服务异常|重新刷新拉取数据/.test(payload.text || "") || !worksLoaded;
    const trustedWorks = extracted.ids.length && !accountWorksFailed;
    const status = trustedWorks ? "success" : (payload.loginHint ? "needs_login_or_verification" : "partial_untrusted");
    const failure = trustedWorks
      ? ""
      : (
          accountWorksFailed
            ? "主页作品区未可信加载，发现的视频 ID 可能来自热门推荐或页脚，不作为账号最近作品。"
            : (payload.loginHint ? "页面疑似需要登录/验证后才能看到作品链接" : "页面已渲染但未发现可信作品 ID，可能仍是 JS 壳或作品列表懒加载")
        );
    return {
      account_name: source.account_name || source.name || "",
      homepage_url: homepage,
      source_role: source.source_role || "",
      column: source.column || "",
      status,
      failure_reason: failure,
      page_title: payload.title || "",
      current_url: payload.url || "",
      video_ids: trustedWorks ? extracted.ids : [],
      video_links: trustedWorks ? extracted.links : [],
      video_cards: trustedWorks ? (payload.videoAnchors || []).filter((item) => extracted.ids.includes(item.id)).map((item) => ({
        video_id: item.id,
        href: item.href,
        url: `https://www.douyin.com/video/${item.id}`,
        text: item.text || "",
      })) : [],
      untrusted_video_ids: trustedWorks ? [] : extracted.ids,
      untrusted_video_links: trustedWorks ? [] : extracted.links,
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
      video_ids: [],
      video_links: [],
    };
  } finally {
    client?.close();
    if (tab?.id) {
      try {
        await browserClient.send("Target.closeTarget", { targetId: tab.id });
      } catch {
        // ignore close failures
      }
    }
  }
}

async function probeAccountWithRetry(cdp, browserClient, source, options) {
  let last = null;
  const attempts = Math.max(1, options.retries || 1);
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const result = await probeAccount(cdp, browserClient, source, options);
    result.attempts = attempt;
    last = result;
    if (result.status === "success") return result;
    if (attempt < attempts) {
      await new Promise((resolve) => setTimeout(resolve, Math.min(5000, 1000 * attempt)));
    }
  }
  return last;
}

async function main() {
  const options = parseArgs();
  fs.mkdirSync(options.outDir, { recursive: true });

  const sources = selectedSources(loadSources(options.config), options);
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
  const browserClient = new CdpClient(version.webSocketDebuggerUrl);
  await browserClient.open();
  try {
    for (const source of sources) {
      rows.push(await probeAccountWithRetry(options.cdp, browserClient, source, options));
    }
  } finally {
    browserClient.close();
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
  const homepageCardItems = buildHomepageCardItems(rows);
  fs.writeFileSync(
    manualJsonl,
    homepageCardItems.map((item) => JSON.stringify(item)).join("\n") + (homepageCardItems.length ? "\n" : ""),
    "utf8",
  );
  resolverResult.manual_jsonl = manualJsonl;
  resolverResult.homepage_card_items = homepageCardItems.length;

  const coverage = buildCoverage(sources, rows);
  coverage.account_limit = options.accountLimit;
  const itemLineage = validateContentItemLineage(rows, homepageCardItems);
  if (!itemLineage.ok) coverage.ok = false;

  const output = {
    ok: coverage.ok,
    status: coverage.ok ? "completed" : "completed_with_failures",
    check_only: false,
    writes_feishu: false,
    cdp_browser: version.Browser || "",
    coverage,
    item_lineage: itemLineage,
    accounts: rows.length,
    discovered_video_links: videoLinks.length,
    resolver: resolverResult,
    rows,
  };
  fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(output, null, 2), "utf8");
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
