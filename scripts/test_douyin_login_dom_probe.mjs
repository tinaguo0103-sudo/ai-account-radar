#!/usr/bin/env node
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { classifyLoginMarkers, isMainModule } from "./douyin_login_dom_probe.mjs";

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

let activeMarkers = {};
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
      result: { result: { type: "object", value: activeMarkers } },
    })), () => socket.destroy());
    pending = Buffer.alloc(0);
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "抖音 CLI 空格 "));
const unicodeScript = path.join(tempRoot, "登录 探针.mjs");
fs.copyFileSync(path.join(here, "douyin_login_dom_probe.mjs"), unicodeScript);

async function runFixture(markers) {
  activeMarkers = markers;
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
