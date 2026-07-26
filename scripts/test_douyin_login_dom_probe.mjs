#!/usr/bin/env node
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { classifyLoginMarkers, classifyPageRisk, isMainModule, probeDocument } from "./douyin_login_dom_probe.mjs";

assert.equal(classifyLoginMarkers({ headerAccountControl: true, headerSelfLink: true }), "logged_in");
assert.equal(classifyLoginMarkers({
  contentAuthorAvatarPresent: true,
  contentAuthorLinkPresent: true,
  loginButton: true,
}), "logged_out");
assert.equal(classifyLoginMarkers({
  contentAuthorAvatarPresent: true,
  contentAuthorLinkPresent: true,
}), "indeterminate");
assert.equal(classifyLoginMarkers({ headerAccountControl: true }), "indeterminate");
assert.equal(classifyLoginMarkers({
  headerAccountControl: true,
  headerSelfLink: true,
  verificationIframe: true,
}), "verification_required");
assert.equal(classifyLoginMarkers({}), "indeterminate");
assert.equal(classifyPageRisk("https://www.douyin.com/passport/challenge", ""), "verification_required");
assert.equal(classifyPageRisk("https://www.douyin.com/", "短信验证"), "verification_required");
assert.equal(classifyPageRisk("https://www.douyin.com/", "首页"), "");

function fixtureNode({
  text = "",
  rect = { left: 10, top: 10, right: 62, bottom: 62, width: 52, height: 52 },
  style = {},
  parentElement = null,
  src = "",
  title = "",
  hidden = false,
  ariaHidden = "false",
} = {}) {
  return {
    isConnected: true,
    hidden,
    parentElement,
    src,
    title,
    style: { display: "block", visibility: "visible", opacity: "1", ...style },
    childNodes: text ? [{ nodeType: 3, nodeValue: text }] : [],
    getAttribute(name) { return name === "aria-hidden" ? ariaHidden : null; },
    getClientRects() { return rect.width > 0 && rect.height > 0 ? [rect] : []; },
    getBoundingClientRect() { return rect; },
  };
}

function inspectFixture({
  accountControls = [],
  selfLinks = [],
  accountMenus = [],
  authorAvatars = [],
  authorLinks = [],
  textNodes = [],
  loginDialogs = [],
  verificationDialogs = [],
  iframes = [],
} = {}) {
  const documentRef = {
    documentElement: { clientWidth: 1280, clientHeight: 720 },
    querySelectorAll(selector) {
      if (selector === "iframe") return iframes;
      if (selector.includes('data-e2e="user-avatar"')) return accountControls;
      if (selector.includes('/user/self')) return selfLinks;
      if (selector.includes('aria-label*="账号"')) return accountMenus;
      if (selector.includes('main img[class*="avatar"]')) return authorAvatars;
      if (selector.includes('main a[href*="/user/"]')) return authorLinks;
      if (selector.includes('login-dialog')) return loginDialogs;
      if (selector.includes('[class*="verify"]')) return verificationDialogs;
      if (selector.startsWith("button, a, span")) return textNodes;
      return [];
    },
  };
  const windowRef = {
    innerWidth: 1280,
    innerHeight: 720,
    getComputedStyle(node) { return node.style; },
  };
  return probeDocument(documentRef, windowRef);
}

const visibleSelfA = fixtureNode();
const visibleSelfB = fixtureNode({ rect: { left: 70, top: 10, right: 104, bottom: 44, width: 34, height: 34 } });
const hiddenVerification = fixtureNode({ src: "https://verify.example", style: { display: "none" } });
const zeroSizeVerification = fixtureNode({
  src: "https://verify.example",
  rect: { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 },
});
const offViewportVerification = fixtureNode({
  src: "https://verify.example",
  rect: { left: 1500, top: 10, right: 1600, bottom: 110, width: 100, height: 100 },
});
const hiddenAncestor = fixtureNode({ style: { visibility: "hidden" } });
const ancestorHiddenVerification = fixtureNode({ src: "https://verify.example", parentElement: hiddenAncestor });
for (const iframe of [hiddenVerification, zeroSizeVerification, offViewportVerification, ancestorHiddenVerification]) {
  const inspected = inspectFixture({ selfLinks: [visibleSelfA, visibleSelfB], iframes: [iframe] });
  assert.equal(inspected.markers.verificationIframe, false);
  assert.equal(classifyLoginMarkers(inspected.markers), "logged_in");
}

const visibleVerification = fixtureNode({ src: "https://verify.example" });
let inspected = inspectFixture({ selfLinks: [visibleSelfA, visibleSelfB], iframes: [visibleVerification] });
assert.equal(inspected.visibility.visibleVerificationMarkerCount, 1);
assert.equal(classifyLoginMarkers(inspected.markers), "verification_required");

const visibleVerificationText = fixtureNode({ text: "安全验证" });
inspected = inspectFixture({ selfLinks: [visibleSelfA, visibleSelfB], textNodes: [visibleVerificationText] });
assert.equal(classifyLoginMarkers(inspected.markers), "verification_required");
for (const text of ["拖动滑块完成验证", "短信验证", "challenge verification"]) {
  inspected = inspectFixture({ selfLinks: [visibleSelfA, visibleSelfB], textNodes: [fixtureNode({ text })] });
  assert.equal(classifyLoginMarkers(inspected.markers), "verification_required");
}

const visibleVerificationDialog = fixtureNode();
inspected = inspectFixture({ selfLinks: [visibleSelfA, visibleSelfB], verificationDialogs: [visibleVerificationDialog] });
assert.equal(classifyLoginMarkers(inspected.markers), "verification_required");

const hiddenLogin = fixtureNode({ text: "登录", style: { opacity: "0" } });
inspected = inspectFixture({ selfLinks: [visibleSelfA, visibleSelfB], textNodes: [hiddenLogin] });
assert.equal(classifyLoginMarkers(inspected.markers), "logged_in");

const visibleLogin = fixtureNode({ text: "登录" });
inspected = inspectFixture({ selfLinks: [visibleSelfA, visibleSelfB], textNodes: [visibleLogin] });
assert.equal(classifyLoginMarkers(inspected.markers), "logged_out");

inspected = inspectFixture({ authorAvatars: [fixtureNode()], authorLinks: [fixtureNode()] });
assert.equal(classifyLoginMarkers(inspected.markers), "indeterminate");
inspected = inspectFixture({ selfLinks: [visibleSelfA] });
assert.equal(classifyLoginMarkers(inspected.markers), "indeterminate");

const here = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(here, "douyin_login_dom_probe.mjs");
assert.equal(isMainModule(pathToFileURL(sourcePath).href, sourcePath), true);

function websocketTextFrame(text) {
  const body = Buffer.from(text);
  if (body.length < 126) return Buffer.concat([Buffer.from([0x81, body.length]), body]);
  const header = Buffer.alloc(4);
  header[0] = 0x81;
  header[1] = 126;
  header.writeUInt16BE(body.length, 2);
  return Buffer.concat([header, body]);
}

function decodeClientFrame(buffer) {
  if (buffer.length < 2) return null;
  if ((buffer[0] & 0x0f) !== 0x01) return null;
  let length = buffer[1] & 0x7f;
  let offset = 2;
  if (length === 126) {
    if (buffer.length < 4) return null;
    length = buffer.readUInt16BE(2);
    offset = 4;
  } else if (length === 127) {
    if (buffer.length < 10) return null;
    length = Number(buffer.readBigUInt64BE(2));
    offset = 10;
  }
  const masked = Boolean(buffer[1] & 0x80);
  const maskOffset = masked ? 4 : 0;
  if (buffer.length < offset + maskOffset + length) return null;
  const mask = masked ? buffer.subarray(offset, offset + 4) : null;
  offset += maskOffset;
  const payload = Buffer.from(buffer.subarray(offset, offset + length));
  if (mask) for (let i = 0; i < payload.length; i += 1) payload[i] ^= mask[i % 4];
  return JSON.parse(payload.toString("utf8"));
}

let activeInspection = { markers: {}, visibility: {} };
const server = http.createServer((request, response) => {
  if (request.url === "/json/list") {
    const port = server.address().port;
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify([{
      id: "fixture-page",
      type: "page",
      url: "https://www.douyin.com/",
      title: "Douyin fixture",
      webSocketDebuggerUrl: `ws://127.0.0.1:${port}/devtools/page/fixture-page`,
    }]));
    return;
  }
  response.statusCode = 404;
  response.end();
});
server.on("upgrade", (request, socket) => {
  const accept = crypto.createHash("sha1")
    .update(`${request.headers["sec-websocket-key"]}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
    .digest("base64");
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  let pending = Buffer.alloc(0);
  socket.on("data", (chunk) => {
    pending = Buffer.concat([pending, chunk]);
    const requestPayload = decodeClientFrame(pending);
    if (!requestPayload) return;
    socket.write(websocketTextFrame(JSON.stringify({
      id: requestPayload.id,
      result: { result: { type: "object", value: activeInspection } },
    })), () => socket.destroy());
    pending = Buffer.alloc(0);
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "抖音 CLI 空格 "));
const unicodeScript = path.join(tempRoot, "登录 探针.mjs");
fs.copyFileSync(path.join(here, "douyin_login_dom_probe.mjs"), unicodeScript);

async function runFixture(markers) {
  activeInspection = {
    markers,
    visibility: {
      viewport: { width: 1280, height: 720 },
      visibleHeaderSelfMarkerCount: markers.multipleHeaderSelfMarkers ? 2 : 0,
      visibleLoginMarkerCount: markers.loginButton || markers.loginDialog ? 1 : 0,
      visibleVerificationMarkerCount: markers.verificationIframe || markers.verificationText ? 1 : 0,
      verificationIframeRects: [],
    },
  };
  const port = server.address().port;
  const child = spawn(process.execPath, [unicodeScript, "--cdp", `http://127.0.0.1:${port}`], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const code = await new Promise((resolve) => child.on("close", resolve));
  const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
  assert.equal(lines.length, 1, `expected one JSON object, stdout=${stdout}, stderr=${stderr}`);
  return { code, payload: JSON.parse(lines[0]), stdout, stderr };
}

const loggedIn = await runFixture({ headerAccountControl: true, headerSelfLink: true });
assert.equal(loggedIn.code, 0);
assert.equal(loggedIn.payload.state, "logged_in");
const spawnEvidence = [{ state: loggedIn.payload.state, exit_code: loggedIn.code }];

for (const [markers, state] of [
  [{ loginButton: true }, "logged_out"],
  [{ verificationIframe: true }, "verification_required"],
  [{ contentAuthorAvatarPresent: true }, "indeterminate"],
]) {
  const result = await runFixture(markers);
  assert.equal(result.code, 4);
  assert.equal(result.payload.state, state);
  spawnEvidence.push({ state: result.payload.state, exit_code: result.code });
}

await new Promise((resolve) => server.close(resolve));
fs.rmSync(tempRoot, { recursive: true, force: true });
console.log(JSON.stringify({
  ok: true,
  unicode_space_cli_path: unicodeScript,
  stdout_contract: "single_json_object",
  cases: spawnEvidence,
}));
