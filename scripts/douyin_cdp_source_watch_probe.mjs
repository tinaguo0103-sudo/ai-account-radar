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
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DEFAULT_CONFIG = path.join(ROOT, "config/content_sources.yaml");
const DEFAULT_OUT = path.join(ROOT, "output/spikes/douyin_cdp_source_watch_probe");
const DEFAULT_CDP = "http://127.0.0.1:9222";
const RESOLVER = path.join(ROOT, "scripts/url_content_resolver.py");

function parseArgs() {
  const args = process.argv.slice(2);
  const options = {
    config: DEFAULT_CONFIG,
    outDir: DEFAULT_OUT,
    cdp: DEFAULT_CDP,
    accountLimit: 3,
    videoLimit: 3,
    waitMs: 7000,
  };
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--config") options.config = args[++i];
    else if (arg === "--out-dir") options.outDir = args[++i];
    else if (arg === "--cdp") options.cdp = args[++i];
    else if (arg === "--account-limit") options.accountLimit = Number(args[++i]);
    else if (arg === "--video-limit") options.videoLimit = Number(args[++i]);
    else if (arg === "--wait-ms") options.waitMs = Number(args[++i]);
  }
  return options;
}

function loadSources(configPath) {
  const text = fs.readFileSync(configPath, "utf8");
  return JSON.parse(text).sources || [];
}

function selectedSources(sources, limit) {
  const roles = new Set(["current_main_competitor", "current_aux_competitor"]);
  return sources
    .filter((source) => source.platform === "抖音" && roles.has(source.source_role))
    .slice(0, limit);
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status} ${url}`);
  return response.json();
}

async function createTab(cdp, url) {
  const endpoint = `${cdp.replace(/\/$/, "")}/json/new?${encodeURIComponent(url)}`;
  const response = await fetch(endpoint, { method: "PUT" });
  if (!response.ok) throw new Error(`Cannot create Chrome tab: HTTP ${response.status}`);
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

async function probeAccount(cdp, source, options) {
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
    tab = await createTab(cdp, homepage);
    client = new CdpClient(tab.webSocketDebuggerUrl);
    await client.open();
    await client.send("Runtime.enable");
    await client.send("Page.enable");
    await client.send("Page.navigate", { url: homepage });
    await new Promise((resolve) => setTimeout(resolve, options.waitMs));
    const result = await client.send("Runtime.evaluate", {
      expression: `(() => {
        const anchors = Array.from(document.querySelectorAll('a[href]')).map(a => a.href).join('\\n');
        const html = document.documentElement ? document.documentElement.outerHTML : '';
        const text = document.body ? document.body.innerText : '';
        return JSON.stringify({
          title: document.title,
          url: location.href,
          anchors,
          text: text.slice(0, 5000),
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
    const accountWorksFailed = /服务异常|重新刷新拉取数据/.test(payload.text || "");
    const trustedWorks = extracted.ids.length && !accountWorksFailed;
    const status = trustedWorks ? "success" : (payload.loginHint ? "needs_login_or_verification" : "partial_untrusted");
    const failure = trustedWorks
      ? ""
      : (
          accountWorksFailed
            ? "主页作品区加载异常，发现的视频 ID 可能来自热门推荐，不作为账号最近作品。"
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
      untrusted_video_ids: trustedWorks ? [] : extracted.ids,
      untrusted_video_links: trustedWorks ? [] : extracted.links,
      text_preview: payload.text || "",
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
        await fetch(`${cdp.replace(/\/$/, "")}/json/close/${tab.id}`);
      } catch {
        // ignore close failures
      }
    }
  }
}

async function main() {
  const options = parseArgs();
  fs.mkdirSync(options.outDir, { recursive: true });

  let version;
  try {
    version = await getJson(`${options.cdp.replace(/\/$/, "")}/json/version`);
  } catch (error) {
    const failure = {
      ok: false,
      status: "cdp_unavailable",
      cdp: options.cdp,
      failure_reason: `无法连接 Chrome DevTools：${error.message}`,
      next_step: "请用抖音小号登录 Chrome，并以远程调试方式启动 Chrome：/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=.local_services/douyin-chrome-profile",
    };
    fs.writeFileSync(path.join(options.outDir, "cdp_probe_results.json"), JSON.stringify(failure, null, 2), "utf8");
    console.log(JSON.stringify(failure, null, 2));
    process.exit(2);
  }

  const sources = selectedSources(loadSources(options.config), options.accountLimit);
  const rows = [];
  for (const source of sources) {
    rows.push(await probeAccount(options.cdp, source, options));
  }
  const videoLinks = Array.from(new Set(rows.flatMap((row) => row.video_links || [])));
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

  const output = {
    ok: true,
    cdp_browser: version.Browser || "",
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
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
