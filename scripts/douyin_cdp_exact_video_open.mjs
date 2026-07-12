#!/usr/bin/env node
/** Read-only exact Douyin video opener over the dedicated local Chrome CDP. */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const DEFAULT_CDP = "http://127.0.0.1:9333";

export function videoIdFromUrl(value) {
  const url = new URL(String(value || ""));
  if (!/(^|\.)douyin\.com$/i.test(url.hostname)) throw new Error("not_douyin_url");
  const match = url.pathname.match(/^\/video\/(\d{10,})\/?$/);
  if (!match) throw new Error("not_exact_video_url");
  return match[1];
}

export function canonicalVideoUrl(value) {
  return `https://www.douyin.com/video/${videoIdFromUrl(value)}`;
}

export function classifyPage(inputVideoId, page) {
  const finalUrl = String(page.final_url || "");
  let finalId = "";
  try { finalId = videoIdFromUrl(finalUrl); } catch { /* typed below */ }
  if (!finalId) return { status: "source_open_failed", reason: "final_url_not_exact_video" };
  if (finalId !== inputVideoId) return { status: "source_open_failed", reason: "video_id_identity_mismatch" };
  if (page.page_state === "video_unavailable") return { status: "source_open_failed", reason: "video_unavailable" };
  if (page.verification_state !== "clear") return { status: "source_open_failed", reason: page.verification_state };
  if (!page.exact_title || !page.visible_text) return { status: "opened_partial", reason: "visible_content_insufficient" };
  return { status: "opened", reason: "" };
}

export function verifyExpectedTitle(expected, visibleDescription) {
  const expectedTitle = String(expected || "").replace(/\s+/g, " ").trim();
  const visible = String(visibleDescription || "").replace(/^第\d+集\s*[|｜]\s*/, "").replace(/\s+/g, " ").trim();
  const proofText = value => value
    .replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu, "")
    .replace(/[👉✅]/g, "")
    .replace(/\s+/g, "");
  const compactExpected = proofText(expectedTitle);
  let compactVisible = ""; let consumed = 0;
  for (const [index, character] of Array.from(visible).entries()) {
    if (proofText(character)) compactVisible += proofText(character);
    consumed = index + 1;
    if (compactVisible.length >= compactExpected.length) break;
  }
  const verified = Boolean(compactExpected && compactVisible === compactExpected);
  return {
    verified,
    exact_title: verified ? expectedTitle : "",
    caption_body: verified ? Array.from(visible).slice(consumed).join("").trim() : "",
  };
}

function parseArgs(argv = process.argv.slice(2)) {
  const options = { cdp: DEFAULT_CDP, url: "", expectedTitle: "", outDir: "", waitMs: 8000 };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--cdp") options.cdp = argv[++index];
    else if (arg === "--url") options.url = argv[++index];
    else if (arg === "--expected-title") options.expectedTitle = argv[++index];
    else if (arg === "--out-dir") options.outDir = argv[++index];
    else if (arg === "--wait-ms") options.waitMs = Number(argv[++index]);
    else throw new Error(`unknown_argument:${arg}`);
  }
  if (!options.url || !options.outDir) throw new Error("--url and --out-dir are required");
  return options;
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

class CdpClient {
  constructor(wsUrl) { this.wsUrl = wsUrl; this.seq = 0; this.pending = new Map(); }
  async open() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("cdp_websocket_timeout")), 10000);
      this.ws.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
      this.ws.addEventListener("error", () => { clearTimeout(timer); reject(new Error("cdp_websocket_error")); }, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      if (!payload.id || !this.pending.has(payload.id)) return;
      const pending = this.pending.get(payload.id); this.pending.delete(payload.id);
      payload.error ? pending.reject(new Error(payload.error.message)) : pending.resolve(payload.result);
    });
  }
  send(method, params = {}) {
    const id = ++this.seq;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => { if (this.pending.delete(id)) reject(new Error(`cdp_timeout:${method}`)); }, 20000);
    });
  }
  close() { try { this.ws?.close(); } catch { /* no-op */ } }
}

async function targetFor(cdp, targetId) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const rows = await getJson(`${cdp}/json/list`);
    const target = rows.find((row) => row.id === targetId);
    if (target?.webSocketDebuggerUrl) return target;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("target_not_found");
}

function sha256(value) { return crypto.createHash("sha256").update(value).digest("hex"); }

async function inspectExactVideo(client) {
  const expression = `(() => {
    const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
    const text = clean(document.body?.innerText || '');
    const html = document.documentElement?.outerHTML || '';
    const metas = Object.fromEntries(Array.from(document.querySelectorAll('meta')).map(node => [
      node.getAttribute('property') || node.getAttribute('name') || '', node.getAttribute('content') || ''
    ]).filter(row => row[0]));
    const h1 = clean(document.querySelector('h1')?.innerText || '');
    const descriptionNode = document.querySelector('[data-e2e="video-desc"], [class*="video-info-detail"], [class*="videoDesc"]');
    const descriptionRaw = String(descriptionNode?.innerText || '').trim();
    const descriptionLines = descriptionRaw.split(/\\n+/).map(clean).filter(Boolean);
    const descriptionParts = descriptionNode ? Array.from(descriptionNode.childNodes)
      .map(node => clean(node.innerText || node.textContent)).filter(Boolean) : [];
    const desc = clean(metas['og:description'] || metas['description'] || '');
    const ogTitle = clean(metas['og:title'] || '');
    const title = h1 || descriptionParts[0] || descriptionLines[0] || ogTitle || clean(document.title || '');
    const focusedAuthor = clean(document.querySelector('[class*="author"] a[href*="/user/"], [class*="Author"] a[href*="/user/"], [data-e2e="video-author-name"]')?.innerText || '');
    const authorCandidates = Array.from(document.querySelectorAll('[data-e2e="video-author-name"], a[href*="/user/"]'))
      .map(node => clean(node.innerText)).filter(value => value && value.length <= 80);
    const author = focusedAuthor || authorCandidates.find(value => !['我的', '关注', '朋友'].includes(value)) || clean(metas['author'] || '');
    const subtitleNodes = Array.from(document.querySelectorAll('[class*="subtitle"], [class*="caption"], [data-e2e*="subtitle"]'));
    const transcript = clean(subtitleNodes.map(node => node.innerText).join(' '));
    const verification = /验证码|安全验证|请完成(?:下列)?验证|滑块验证/.test(text)
      ? 'needs_verification' : (/请登录后|扫码登录|手机号登录/.test(text) ? 'needs_login' : 'clear');
    const pageState = /你要观看的视频不存在|视频已删除|作品不存在/.test(text) ? 'video_unavailable' : 'video_page';
    const captionBody = descriptionParts.length > 1 ? descriptionParts.slice(1).join(' ') :
      (descriptionLines.length > 1 ? descriptionLines.slice(1).join(' ') : (descriptionRaw ? '' : desc));
    const publishText = (text.match(/发布时间[:：]?\\s*([0-9-]{8,10}[^\\s]{0,10})/) || [])[1] || '';
    return JSON.stringify({ final_url: location.href, document_title: clean(document.title), exact_title: title,
      caption_body: captionBody, description_parts: descriptionParts.slice(0, 20), author, author_candidates: authorCandidates.slice(0, 10),
      publish_metadata: clean(document.querySelector('time')?.innerText || publishText),
      transcript, transcript_state: transcript ? 'visible' : 'absent', visible_text: text.slice(0, 12000),
      verification_state: verification, page_state: pageState, html_identity_sample: html.slice(0, 20000) });
  })()`;
  const evaluated = await client.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  return JSON.parse(evaluated.result.value || "{}");
}

export async function openExactVideo(options) {
  const inputUrl = canonicalVideoUrl(options.url);
  const inputVideoId = videoIdFromUrl(inputUrl);
  const outDir = path.resolve(options.outDir);
  fs.mkdirSync(outDir, { recursive: true });
  const cdp = String(options.cdp || DEFAULT_CDP).replace(/\/$/, "");
  const browserVersion = await getJson(`${cdp}/json/version`);
  const browser = new CdpClient(browserVersion.webSocketDebuggerUrl);
  let targetId = ""; let page;
  await browser.open();
  try {
    const created = await browser.send("Target.createTarget", { url: "about:blank", background: true });
    targetId = created.targetId;
    const target = await targetFor(cdp, targetId);
    const client = new CdpClient(target.webSocketDebuggerUrl);
    await client.open();
    try {
      await client.send("Page.enable"); await client.send("Runtime.enable");
      await client.send("Page.navigate", { url: inputUrl });
      await new Promise((resolve) => setTimeout(resolve, options.waitMs || 8000));
      page = await inspectExactVideo(client);
      const screenshot = await client.send("Page.captureScreenshot", { format: "png", fromSurface: true });
      fs.writeFileSync(path.join(outDir, "page.png"), Buffer.from(screenshot.data, "base64"));
    } finally { client.close(); }
  } finally {
    if (targetId) { try { await browser.send("Target.closeTarget", { targetId }); } catch { /* no-op */ } }
    browser.close();
  }
  const classification = classifyPage(inputVideoId, page || {});
  const titleResult = verifyExpectedTitle(options.expectedTitle, page.exact_title);
  if (classification.status === "opened" && !titleResult.verified) {
    classification.status = "opened_partial";
    classification.reason = "expected_title_not_verified_on_exact_page";
  }
  const exactTitle = titleResult.exact_title;
  const captionBody = titleResult.verified ? titleResult.caption_body : String(page.caption_body || "").trim();
  const contentForHash = JSON.stringify({
    video_id: inputVideoId, final_url: page.final_url, exact_title: page.exact_title,
    caption_body: page.caption_body, transcript: page.transcript, visible_text: page.visible_text,
  });
  const result = {
    protocol: "ar020d_douyin_exact_source_v1", input_url: inputUrl, exact_url: inputUrl,
    primary_adapter: "douyin_cdp_exact_video_v1",
    attempted_adapters: ["douyin_cdp_exact_video_v1"],
    final_url: page.final_url || "", input_video_id: inputVideoId,
    final_video_id: (() => { try { return videoIdFromUrl(page.final_url); } catch { return ""; } })(),
    identity_match: classification.reason !== "video_id_identity_mismatch" && Boolean(page.final_url),
    open_status: classification.status, failure_reason: classification.reason,
    login_state: page.verification_state === "needs_login" ? "required" : "available",
    verification_state: page.verification_state || "unknown", exact_title: exactTitle,
    title_verification_state: titleResult.verified ? "visible_prefix_match" : "unverified",
    page_state: page.page_state || "unknown",
    page_identity: { kind: "concrete_url", path: `/video/${inputVideoId}` },
    adapter_version: "ar020d_primary_exact_source_adapter_v1",
    browser_surface: "local_chrome_cdp_9333",
    browser_session_boundary: "dedicated_douyin_profile_read_only_target",
    source_summary: captionBody || page.visible_text?.slice(0, 1000) || "",
    caption_body: captionBody, author: page.author || "", platform: "抖音",
    extraction_debug: { description_parts: page.description_parts || [], author_candidates: page.author_candidates || [] },
    visible_description: page.exact_title || "",
    publish_metadata: page.publish_metadata || "", transcript: page.transcript || "",
    transcript_state: page.transcript_state || "absent", visible_text: page.visible_text || "",
    source_type: "douyin_exact_video", opened_at: new Date().toISOString(),
    captured_content_hash: sha256(contentForHash), page_content_hash: sha256(contentForHash),
    screenshot_path: path.join(outDir, "page.png"), retrieval_surface: "dedicated_local_chrome_cdp",
    content_evidence: classification.status === "opened" && page.visible_text ? [{
      evidence_id: `douyin-page-${inputVideoId}`,
      evidence_type: page.transcript ? "visible_page_and_transcript" : "visible_page_metadata",
      text: (page.transcript || page.caption_body || page.visible_text).slice(0, 2000),
    }] : [],
    boundary: "read_only_exact_video; no resolver; no CSV/search/old-payload substitution",
  };
  fs.writeFileSync(path.join(outDir, "source_open.json"), JSON.stringify(result, null, 2));
  return result;
}

async function main() {
  try {
    const result = await openExactVideo(parseArgs());
    console.log(JSON.stringify(result, null, 2));
    process.exitCode = result.open_status === "opened" ? 0 : 2;
  } catch (error) {
    console.error(JSON.stringify({ ok: false, open_status: "source_open_failed", failure_reason: error.message }, null, 2));
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === new URL(`file://${path.resolve(process.argv[1])}`).href) await main();
