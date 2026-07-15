#!/usr/bin/env node
import process from "node:process";
import path from "node:path";
import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

export function classifyLoginMarkers(markers) {
  if (markers.verificationIframe || markers.verificationDialog || markers.verificationText) return "verification_required";
  if (markers.loginButton || markers.loginDialog) return "logged_out";
  if (markers.multipleHeaderSelfMarkers) return "logged_in";
  const positiveCount = [markers.headerAccountControl, markers.headerSelfLink, markers.headerAccountMenu]
    .filter(Boolean).length;
  if (positiveCount >= 2) return "logged_in";
  return "indeterminate";
}

export function probeDocument(documentRef, windowRef) {
  const viewportWidth = Number(windowRef?.innerWidth || documentRef?.documentElement?.clientWidth || 0);
  const viewportHeight = Number(windowRef?.innerHeight || documentRef?.documentElement?.clientHeight || 0);
  const styleFor = (node) => windowRef.getComputedStyle(node);
  const rectFor = (node) => node.getBoundingClientRect();
  const isVisible = (node) => {
    if (!node || node.isConnected !== true || viewportWidth <= 0 || viewportHeight <= 0) return false;
    for (let current = node; current; current = current.parentElement) {
      const style = styleFor(current);
      const opacity = Number.parseFloat(style?.opacity ?? "1");
      if (
        current.hidden === true
        || current.getAttribute?.("aria-hidden") === "true"
        || style?.display === "none"
        || style?.visibility === "hidden"
        || style?.visibility === "collapse"
        || !Number.isFinite(opacity)
        || opacity <= 0
      ) return false;
    }
    if (!node.getClientRects || node.getClientRects().length === 0) return false;
    const rect = rectFor(node);
    return rect.width > 0
      && rect.height > 0
      && rect.right > 0
      && rect.bottom > 0
      && rect.left < viewportWidth
      && rect.top < viewportHeight;
  };
  const nodes = (selector) => Array.from(documentRef.querySelectorAll(selector));
  const visibleNodes = (selector) => nodes(selector).filter(isVisible);
  const directText = (node) => Array.from(node.childNodes || [])
    .filter((child) => child.nodeType === 3)
    .map((child) => child.nodeValue || "")
    .join(" ")
    .trim();
  const unique = (items) => Array.from(new Set(items));

  const accountControls = visibleNodes('header [data-e2e="user-avatar"], nav [data-e2e="user-avatar"], [role="navigation"] [data-e2e="user-profile"]');
  const selfLinks = visibleNodes('a[href*="/user/self"]');
  const accountMenus = visibleNodes('header [aria-label*="账号"], nav [aria-label*="账号"], [role="navigation"] [data-e2e="account-menu"]');
  const distinctHeaderSelfMarkers = unique([...accountControls, ...selfLinks, ...accountMenus]);
  const contentAuthorAvatars = visibleNodes('main img[class*="avatar"], [data-e2e="feed"] img[class*="avatar"]');
  const contentAuthorLinks = visibleNodes('main a[href*="/user/"], [data-e2e="feed"] a[href*="/user/"]');
  const visibleTextNodes = visibleNodes('button, a, span, p, div, h1, h2, h3, [role="dialog"]');
  const loginButtons = visibleTextNodes.filter((node) => /^登录$/.test(directText(node)));
  const loginDialogs = visibleNodes('[class*="login-dialog"], [class*="login-container"]');
  const verificationIframes = visibleNodes('iframe')
    .filter((node) => /captcha|verify|verification/i.test(node.src || node.title || ""));
  const verificationDialogs = visibleNodes('[class*="verify"], [class*="captcha"], [id*="verify"], [id*="captcha"], [role="dialog"][aria-label*="验证"]');
  const verificationTextNodes = visibleTextNodes
    .filter((node) => /验证码|安全验证|完成验证|captcha|verification/i.test(directText(node)));
  const rectSummary = (node) => {
    const rect = rectFor(node);
    return { width: Math.round(rect.width), height: Math.round(rect.height), visible: isVisible(node) };
  };

  return {
    markers: {
      headerAccountControl: accountControls.length > 0,
      headerSelfLink: selfLinks.length > 0,
      headerAccountMenu: accountMenus.length > 0,
      multipleHeaderSelfMarkers: distinctHeaderSelfMarkers.length >= 2,
      contentAuthorAvatarPresent: contentAuthorAvatars.length > 0,
      contentAuthorLinkPresent: contentAuthorLinks.length > 0,
      loginButton: loginButtons.length > 0,
      loginDialog: loginDialogs.length > 0,
      verificationIframe: verificationIframes.length > 0,
      verificationDialog: verificationDialogs.length > 0,
      verificationText: verificationTextNodes.length > 0,
    },
    visibility: {
      viewport: { width: viewportWidth, height: viewportHeight },
      visibleHeaderSelfMarkerCount: distinctHeaderSelfMarkers.length,
      visibleLoginMarkerCount: unique([...loginButtons, ...loginDialogs]).length,
      visibleVerificationMarkerCount: unique([...verificationIframes, ...verificationDialogs, ...verificationTextNodes]).length,
      verificationIframeRects: nodes('iframe')
        .filter((node) => /captcha|verify|verification/i.test(node.src || node.title || ""))
        .map(rectSummary),
    },
  };
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function evaluate(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("websocket_timeout")), 5000);
    ws.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
    ws.addEventListener("error", () => { clearTimeout(timer); reject(new Error("websocket_error")); }, { once: true });
  });
  const expression = `(${probeDocument.toString()})(document, window)`;
  const result = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("runtime_evaluate_timeout")), 5000);
    ws.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      if (payload.id === 1) { clearTimeout(timer); resolve(payload); }
    });
    ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression, returnByValue: true } }));
  });
  ws.close();
  if (result.error) throw new Error(result.error.message || "runtime_evaluate_failed");
  return result.result?.result?.value || {};
}

export async function inspectDouyinLogin(cdp) {
  const targets = await getJson(`${cdp.replace(/\/$/, "")}/json/list`);
  const pages = targets.filter((item) => item.type === "page" && /^https?:\/\/([^/]+\.)?douyin\.com\//i.test(item.url || ""));
  if (!pages.length) return { state: "indeterminate", url: "", title: "", markers: {}, error: "douyin_page_not_found" };
  const page = pages[0];
  const inspected = await evaluate(page.webSocketDebuggerUrl);
  const markers = inspected.markers || {};
  return {
    state: classifyLoginMarkers(markers),
    url: page.url || "",
    title: page.title || "",
    markers,
    visibility: inspected.visibility || {},
  };
}

export function isMainModule(metaUrl, argvPath) {
  if (!argvPath) return false;
  try {
    return realpathSync(path.resolve(fileURLToPath(metaUrl))) === realpathSync(path.resolve(argvPath));
  } catch {
    return false;
  }
}

if (isMainModule(import.meta.url, process.argv[1])) {
  const cdpIndex = process.argv.indexOf("--cdp");
  const cdp = cdpIndex >= 0 ? process.argv[cdpIndex + 1] : "http://127.0.0.1:9333";
  try {
    const result = await inspectDouyinLogin(cdp);
    console.log(JSON.stringify(result));
    process.exitCode = result.state === "logged_in" ? 0 : 4;
  } catch (error) {
    console.log(JSON.stringify({ state: "indeterminate", url: "", title: "", markers: {}, error: String(error.message || error) }));
    process.exitCode = 4;
  }
}
