#!/usr/bin/env node
/** Resolve fresh public media URLs for an exact bounded selected-video list. */
import fs from "node:fs";
import path from "node:path";
import {
  FixedPageSession,
  fixedDouyinTarget,
  runDouyinPreflight,
} from "./douyin_cdp_source_watch_probe.mjs";

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
      await session.send("Page.navigate", { url: candidate.source_url });
      await sleep(options.waitMs);
      await risk(`after_${candidate.aweme_id}`);
      const media = decode(
        await session.send("Runtime.evaluate", {
          expression: `JSON.stringify({
            url: location.href,
            title: document.title,
            media: [
              ...[...document.querySelectorAll('video')]
                .map((video) => video.currentSrc || video.src || ''),
              ...performance.getEntriesByType('resource')
                .map((entry) => entry.name)
                .filter((value) => /douyinvod\\.com/.test(value)
                  && (/\\/video\\/tos\\//.test(value) || /mime_type=video_mp4/.test(value)))
            ].filter((value) => /^https?:\\/\\//.test(value))
          })`,
          returnByValue: true,
        }),
        `media_dom_indeterminate:${candidate.aweme_id}`,
      );
      const exactPath = new URL(media.url).pathname.replace(/\/$/, "");
      const expectedPath = new URL(candidate.source_url).pathname.replace(/\/$/, "");
      const publicMedia = [...new Set(media.media)];
      const videoUrl = publicMedia.find((value) => /media-video-/i.test(value))
        || publicMedia.find((value) => !/media-audio-/i.test(value)) || "";
      const audioUrl = publicMedia.find((value) => /media-audio-/i.test(value)) || "";
      resolved.push({
        ...candidate,
        playable_url: exactPath === expectedPath ? videoUrl : "",
        audio_url: exactPath === expectedPath ? audioUrl : "",
        media_resolution_status: (
          exactPath !== expectedPath ? "identity_conflict"
            : videoUrl ? "resolved" : "media_unavailable"
        ),
      });
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
