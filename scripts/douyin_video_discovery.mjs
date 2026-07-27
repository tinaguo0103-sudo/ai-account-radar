#!/usr/bin/env node
/** Bounded discovery on the one existing fixed Douyin page. */
import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import {
  FixedPageSession,
  fixedDouyinTarget,
  runDouyinPreflight,
} from "./douyin_cdp_source_watch_probe.mjs";

function parseArgs(argv) {
  const out = { cdp: "http://127.0.0.1:9333", output: "", waitMs: 8000, query: "AI" };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "--cdp") out.cdp = argv[++i];
    else if (key === "--output") out.output = argv[++i];
    else if (key === "--wait-ms") out.waitMs = Number(argv[++i]);
    else if (key === "--query") out.query = argv[++i];
    else throw new Error(`unknown_argument:${key}`);
  }
  if (!out.output) throw new Error("discovery_output_missing");
  return out;
}

function writeAtomicJson(output, payload) {
  const target = path.resolve(output);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temporary = path.join(
    path.dirname(target),
    `.${path.basename(target)}.${process.pid}.${Date.now()}.tmp`,
  );
  const descriptor = fs.openSync(temporary, "wx");
  try {
    fs.writeFileSync(descriptor, `${JSON.stringify(payload, null, 2)}\n`);
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
  fs.renameSync(temporary, target);
}

function riskExpression() {
  return `(() => {
    const visible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 2 && rect.height > 2 && style.visibility !== 'hidden'
        && style.display !== 'none';
    };
    const frames = [...document.querySelectorAll('iframe')].filter((frame) =>
      /rc-verifycenter|rmc-nocaptcha|captcha|verify/i.test(frame.src || '') && visible(frame));
    const body = document.body?.innerText || '';
    const verificationText = /(验证码|滑块验证|安全验证|短信验证)/.test(body);
    const loginButton = [...document.querySelectorAll('button,a')].some((element) =>
      visible(element) && /^(登录|立即登录)$/.test((element.textContent || '').trim()));
    return JSON.stringify({
      clear: frames.length === 0 && !verificationText && !loginButton,
      frame_count: frames.length,
      verification_text: verificationText,
      login_button: loginButton,
      url: location.href,
      title: document.title,
    });
  })()`;
}

function decodeEvaluation(result) {
  if (result?.exceptionDetails || typeof result?.result?.value !== "string") {
    throw new Error("discovery_risk_preflight_indeterminate");
  }
  return JSON.parse(result.result.value);
}

function candidateItems(value) {
  if (!value || typeof value !== "object") return [];
  if (Array.isArray(value.aweme_list)) return value.aweme_list;
  if (Array.isArray(value.data)) {
    return value.data.map((item) => item?.aweme_info || item).filter(Boolean);
  }
  return [];
}

function firstUrl(value) {
  if (Array.isArray(value)) return String(value[0] || "");
  return String(value || "");
}

function deriveSearchQuery(candidates, fallback, currentUrl) {
  const current = decodeURIComponent(new URL(currentUrl || "https://www.douyin.com/").pathname);
  const tokens = [];
  for (const item of candidates) {
    const title = String(item.title || "");
    for (const match of title.matchAll(/#([^#\s]{2,18})|\b(AI|AIGC|Agent|Prompt|GPT|Claude|Vibecoding)\b/gi)) {
      const token = String(match[1] || match[2] || "").trim();
      if (token && !tokens.includes(token)) tokens.push(token);
    }
  }
  const candidatesQuery = tokens.slice(0, 3).join(" ");
  const query = candidatesQuery || fallback || "AI 工具 人工智能";
  if (current.includes(query)) return `${query} 教程`;
  return query;
}

function normalize(item, source) {
  const stats = item.statistics || {};
  const video = item.video || {};
  const awemeId = String(item.aweme_id || "");
  const mediaUrl = firstUrl(video.play_addr?.url_list);
  return {
    run_id: "",
    discovery_source: source,
    aweme_id: awemeId,
    source_url: awemeId ? `https://www.douyin.com/video/${awemeId}` : "",
    author: String(item.author?.nickname || ""),
    title: String(item.desc || ""),
    published_at: String(item.create_time || ""),
    duration_seconds: Math.max(1, Math.round(Number(video.duration || 0) / 1000)),
    likes: Number(stats.digg_count || 0),
    comments: Number(stats.comment_count || 0),
    favorites: Number(stats.collect_count || 0),
    shares: Number(stats.share_count || 0),
    playable_url: mediaUrl,
    raw_identity: crypto.createHash("sha256")
      .update(JSON.stringify({ awemeId, mediaUrl, createTime: item.create_time || 0 }))
      .digest("hex"),
  };
}

function parseVisibleCount(value) {
  const text = String(value || "").trim().toLowerCase();
  const match = text.match(/^([\d.]+)\s*(万|w)?$/i);
  if (!match) return 0;
  return Math.round(Number(match[1]) * (match[2] ? 10000 : 1));
}

function normalizeVisibleCard(card, source) {
  const awemeId = String(card.href || "").match(/\/video\/(\d{10,})/)?.[1] || "";
  const lines = String(card.text || "").split("\n").map((item) => item.trim()).filter(Boolean);
  const durationIndex = lines.findIndex((item) => /^\d{1,2}:\d{2}$/.test(item));
  const durationText = durationIndex >= 0 ? lines[durationIndex] : "00:00";
  const [minutes, seconds] = durationText.split(":").map(Number);
  const countIndex = durationIndex + 1;
  const authorIndex = lines.findIndex((item, index) => index > countIndex && item.startsWith("@"));
  const title = lines.slice(countIndex + 1, authorIndex > countIndex ? authorIndex : undefined).join(" ");
  const author = authorIndex >= 0 ? lines[authorIndex].replace(/^@/, "") : "";
  return {
    run_id: "",
    discovery_source: source,
    aweme_id: awemeId,
    source_url: awemeId ? `https://www.douyin.com/video/${awemeId}` : "",
    author,
    title,
    published_at: authorIndex >= 0 ? String(lines[authorIndex + 1] || "") : "",
    duration_seconds: Math.max(1, minutes * 60 + seconds),
    likes: parseVisibleCount(lines[countIndex]),
    comments: 0,
    favorites: 0,
    shares: 0,
    playable_url: "",
    raw_identity: crypto.createHash("sha256")
      .update(JSON.stringify({ awemeId, title, author, durationText, visibleCount: lines[countIndex] }))
      .digest("hex"),
  };
}

async function sleep(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function collectVisibleFeed(session, waitMs, risk, stage) {
  await session.send("Page.reload", { ignoreCache: true });
  await sleep(waitMs);
  await risk(`${stage}_after_reload`);
  await session.send("Runtime.evaluate", {
    expression: "window.scrollBy({top: Math.min(window.innerHeight * 2, 1600), behavior: 'instant'}); true",
    returnByValue: true,
  });
  await sleep(Math.min(waitMs, 4000));
  await risk(`${stage}_after_scroll`);
  const result = await session.send("Runtime.evaluate", {
    expression: `JSON.stringify([...document.querySelectorAll('a[href*="/video/"]')]
      .slice(0, 80)
      .map((node) => ({href: node.href, text: node.innerText || node.getAttribute('aria-label') || ''})))`,
    returnByValue: true,
  });
  if (result?.exceptionDetails || typeof result?.result?.value !== "string") {
    throw new Error(`discovery_visible_cards_indeterminate:${stage}`);
  }
  return JSON.parse(result.result.value);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const preflight = runDouyinPreflight();
  if (preflight.status !== "session_verified" || preflight.login_state !== "logged_in") {
    throw new Error(`discovery_preflight_blocked:${preflight.status || preflight.login_state}`);
  }
  const target = await fixedDouyinTarget(options.cdp);
  const initialTargets = await (await fetch(`${options.cdp}/json/list`)).json();
  const session = new FixedPageSession(options.cdp, target, { maxReattachments: 1 });
  const captured = [];
  let source = "recommendation";
  await session.open();
  session.client.on("Network.responseReceived", async ({ requestId, response, type }) => {
    if (String(type || "").toUpperCase() !== "XHR"
        || !/feed|aweme|search/i.test(String(response?.url || ""))) return;
    try {
      const body = await session.send("Network.getResponseBody", { requestId });
      const decoded = JSON.parse(body.body);
      for (const item of candidateItems(decoded)) {
        const row = normalize(item, source);
        if (row.aweme_id && row.playable_url) captured.push(row);
      }
    } catch {
      // A page-owned non-JSON response is not a candidate.
    }
  });
  const risk = async (stage) => {
    const state = decodeEvaluation(await session.send("Runtime.evaluate", {
      expression: riskExpression(), returnByValue: true,
    }));
    if (!state.clear) throw new Error(`discovery_risk_detected:${stage}`);
  };
  try {
    await risk("before_navigation");
    await session.send("Page.navigate", {
      url: "https://www.douyin.com/?recommend=1&from_nav=1",
    });
    const recommendationCards = await collectVisibleFeed(
      session, options.waitMs, risk, "recommendation",
    );
    captured.push(...recommendationCards.map((card) => normalizeVisibleCard(card, "recommendation")));
    source = "dynamic_search";
    const query = encodeURIComponent(deriveSearchQuery(captured, options.query, target.url));
    await session.send("Page.navigate", { url: `https://www.douyin.com/search/${query}?type=video` });
    const searchCards = await collectVisibleFeed(session, options.waitMs, risk, "dynamic_search");
    captured.push(...searchCards.map((card) => normalizeVisibleCard(card, "dynamic_search")));
  } finally {
    session.close();
  }
  const finalTargets = await (await fetch(`${options.cdp}/json/list`)).json();
  const candidates = [...new Map(captured.map((row) => [row.aweme_id, row])).values()];
  const payload = {
    schema_version: 1,
    status: "completed",
    fixed_target_id: String(target.id),
    page_count_before: initialTargets.filter((item) => item.type === "page").length,
    page_count_after: finalTargets.filter((item) => item.type === "page").length,
    page_lifecycle_mutations: 0,
    credential_reads: 0,
    captcha_actions: 0,
    candidates,
  };
  writeAtomicJson(options.output, payload);
  process.stdout.write(`${JSON.stringify({ ok: true, status: payload.status, count: candidates.length })}\n`);
}

main().catch((error) => {
  process.stdout.write(`${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`);
  process.exitCode = 2;
});
