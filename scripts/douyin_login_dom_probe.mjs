#!/usr/bin/env node
import process from "node:process";

export function classifyLoginMarkers(markers) {
  if (markers.verificationIframe || markers.verificationText) return "verification_required";
  if (markers.loginButton || markers.loginDialog) return "logged_out";
  const positiveCount = [markers.headerAccountControl, markers.headerSelfLink, markers.headerAccountMenu]
    .filter(Boolean).length;
  if (positiveCount >= 2) return "logged_in";
  return "indeterminate";
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
  const expression = `(() => {
    const text = (document.body?.innerText || "").slice(0, 200000);
    const hasText = (pattern) => pattern.test(text);
    return {
      headerAccountControl: Boolean(document.querySelector('header [data-e2e="user-avatar"], nav [data-e2e="user-avatar"], [role="navigation"] [data-e2e="user-profile"]')),
      headerSelfLink: Boolean(document.querySelector('header a[href*="/user/self"], nav a[href*="/user/self"], [role="navigation"] a[href*="/user/self"]')),
      headerAccountMenu: Boolean(document.querySelector('header [aria-label*="账号"], nav [aria-label*="账号"], [role="navigation"] [data-e2e="account-menu"]')),
      contentAuthorAvatarPresent: Boolean(document.querySelector('main img[class*="avatar"], [data-e2e="feed"] img[class*="avatar"]')),
      contentAuthorLinkPresent: Boolean(document.querySelector('main a[href*="/user/"], [data-e2e="feed"] a[href*="/user/"]')),
      loginButton: Array.from(document.querySelectorAll('button, a, span')).some((node) => /^登录$/.test((node.textContent || '').trim())),
      loginDialog: Boolean(document.querySelector('[class*="login-dialog"], [class*="login-container"]')),
      verificationIframe: Array.from(document.querySelectorAll('iframe')).some((node) => /captcha|verify|verification/i.test(node.src || node.title || '')),
      verificationText: hasText(/验证码|安全验证|完成验证|captcha|verification/i)
    };
  })()`;
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
  const markers = await evaluate(page.webSocketDebuggerUrl);
  return { state: classifyLoginMarkers(markers), url: page.url || "", title: page.title || "", markers };
}

if (import.meta.url === `file://${process.argv[1]}`) {
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
