#!/usr/bin/env node
/** Resolve fresh public media URLs for an exact bounded selected-video list. */
import fs from "node:fs";
import path from "node:path";
import {
  FixedPageSession,
  fixedDouyinTarget,
  runDouyinPreflight,
} from "./douyin_cdp_source_watch_probe.mjs";
import { awemeIdFromUrl, resolveOwnedMedia } from "./douyin_video_media_identity.mjs";

function parseArgs(argv) {
  const out = { cdp: "http://127.0.0.1:9333", input: "", output: "", waitMs: 5000 };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--cdp") out.cdp = argv[++index];
    else if (key === "--input") out.input = argv[++index];
    else if (key === "--output") out.output = argv[++index];
    else if (key === "--wait-ms") out.waitMs = Number(argv[++index]);
    else throw new Error(`unknown_argument:${key}`);
  }
  if (!out.input || !out.output) throw new Error("media_resolver_binding_missing");
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
    });
  })()`;
}

function decode(result, error) {
  if (result?.exceptionDetails || typeof result?.result?.value !== "string") {
    throw new Error(error);
  }
  return JSON.parse(result.result.value);
}

async function sleep(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const preflight = runDouyinPreflight();
  if (preflight.status !== "session_verified" || preflight.login_state !== "logged_in") {
    throw new Error(`media_preflight_blocked:${preflight.status || preflight.login_state}`);
  }
  const candidates = JSON.parse(fs.readFileSync(options.input, "utf8"));
  if (!Array.isArray(candidates) || candidates.length > 20) {
    throw new Error("media_resolver_candidate_budget_invalid");
  }
  const target = await fixedDouyinTarget(options.cdp);
  const initialTargets = await (await fetch(`${options.cdp}/json/list`)).json();
  const session = new FixedPageSession(options.cdp, target, { maxReattachments: 1 });
  const resolved = [];
  await session.open();
  if (typeof session.client.on !== "function") throw new Error("media_network_capture_unavailable");
  let activeCapture = null;
  session.client.on("Network.requestWillBeSent", ({ requestId, request }) => {
    if (!activeCapture || !request?.url) return;
    try {
      const parsed = new URL(request.url);
      const requestedId = parsed.searchParams.get("aweme_id") || parsed.searchParams.get("item_id") || "";
      if (
        request.method === "GET"
        && requestedId === activeCapture.requestedId
        && /\/aweme\/v\d+\/web\/aweme\/detail\/?$/i.test(parsed.pathname)
      ) {
        activeCapture.requests.set(requestId, { requestUrl: request.url });
      }
    } catch {
      // Malformed or unrelated request URLs are not ownership evidence.
    }
  });
  session.client.on("Network.responseReceived", (params) => {
    const request = activeCapture?.requests.get(params.requestId);
    if (!request || !["XHR", "FETCH"].includes(String(params.type || "").toUpperCase())) return;
    request.responseUrl = params.response?.url || request.requestUrl;
    request.httpStatus = params.response?.status || 0;
    request.mimeType = params.response?.mimeType || "";
  });
  session.client.on("Network.loadingFinished", ({ requestId }) => {
    const request = activeCapture?.requests.get(requestId);
    if (!request || !request.responseUrl) return;
    activeCapture.requests.delete(requestId);
    const capture = activeCapture;
    const pending = session.client.send("Network.getResponseBody", { requestId })
      .then((response) => capture.details.push({
        requestUrl: request.requestUrl,
        responseUrl: request.responseUrl,
        httpStatus: request.httpStatus,
        mimeType: request.mimeType,
        body: response?.body || "",
        base64Encoded: Boolean(response?.base64Encoded),
      }))
      .catch(() => capture.details.push({
        requestUrl: request.requestUrl,
        responseUrl: request.responseUrl,
        httpStatus: request.httpStatus,
        mimeType: request.mimeType,
        body: "",
      }));
    capture.pending.push(pending);
  });
  const risk = async (stage) => {
    const state = decode(
      await session.send("Runtime.evaluate", {
        expression: riskExpression(), returnByValue: true,
      }),
      `media_risk_indeterminate:${stage}`,
    );
    if (!state.clear) throw new Error(`media_risk_detected:${stage}`);
  };
  try {
    await risk("before_first_navigation");
    for (const candidate of candidates) {
      await risk(`before_${candidate.aweme_id}`);
      activeCapture = {
        requestedId: String(candidate.aweme_id || ""),
        requests: new Map(), details: [], pending: [],
      };
      await session.send("Page.navigate", { url: candidate.source_url });
      await sleep(options.waitMs);
      await Promise.allSettled(activeCapture.pending);
      await risk(`after_${candidate.aweme_id}`);
      const media = decode(
        await session.send("Runtime.evaluate", {
          expression: `JSON.stringify({
            url: location.href,
            title: document.title,
            media: [
              ...[...document.querySelectorAll('video')]
                .map((video) => video.currentSrc || video.src || ''),
              ...[...document.querySelectorAll('audio')]
                .map((audio) => audio.currentSrc || audio.src || ''),
              ...performance.getEntriesByType('resource')
                .map((entry) => entry.name)
                .filter((value) => /douyinvod\\.com/.test(value)
                  && (/\\/video\\/tos\\//.test(value) || /mime_type=video_mp4/.test(value)
                    || /media-audio-/i.test(value)))
            ].filter((value) => /^https?:\\/\\//.test(value))
          })`,
          returnByValue: true,
        }),
        `media_dom_indeterminate:${candidate.aweme_id}`,
      );
      const ownedResults = activeCapture.details.map((detail) => resolveOwnedMedia({
        ...detail,
        requestedId: candidate.aweme_id,
        landedUrl: media.url,
        observedResources: media.media,
      }));
      const verified = ownedResults.find((row) => row.media_ownership?.status === "response_media_identity_verified");
      const conflict = ownedResults.find((row) => row.media_resolution_status === "identity_conflict");
      const ownershipResult = verified || conflict || null;
      const resolution = ownershipResult || {
        media_resolution_status: "media_not_extracted",
        item_type: "unknown",
        playable_url: "",
        audio_url: "",
        media_ownership: {
          status: "owned_detail_response_missing",
          requested_id: String(candidate.aweme_id || ""),
          landed_id: awemeIdFromUrl(media.url),
          response_id: "",
        },
      };
      resolved.push({
        ...candidate,
        landed_url: media.url,
        item_type: resolution.item_type,
        playable_url: resolution.playable_url,
        audio_url: resolution.audio_url,
        media_resolution_status: resolution.media_resolution_status,
        media_ownership: resolution.media_ownership,
      });
      activeCapture = null;
    }
  } finally {
    session.close();
  }
  const finalTargets = await (await fetch(`${options.cdp}/json/list`)).json();
  const output = {
    status: "completed",
    page_count_before: initialTargets.filter((item) => item.type === "page").length,
    page_count_after: finalTargets.filter((item) => item.type === "page").length,
    page_lifecycle_mutations: 0,
    credential_reads: 0,
    captcha_actions: 0,
    candidates: resolved,
  };
  writeAtomicJson(options.output, output);
  process.stdout.write(`${JSON.stringify({
    ok: true,
    resolved: resolved.filter((row) => row.media_resolution_status === "resolved").length,
    failed: resolved.filter((row) => row.media_resolution_status !== "resolved").length,
  })}\n`);
}

main().catch((error) => {
  process.stdout.write(`${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`);
  process.exitCode = 2;
});
