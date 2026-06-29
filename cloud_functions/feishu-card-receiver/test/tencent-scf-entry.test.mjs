import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { main_handler } = require("../tencent-scf/index.js");

function bodyJson(response) {
  return JSON.parse(response.body || "{}");
}

test("Tencent SCF entry returns Feishu challenge", async () => {
  const response = await main_handler({
    httpMethod: "POST",
    body: JSON.stringify({ challenge: "hello" }),
  });
  assert.equal(response.statusCode, 200);
  assert.deepEqual(bodyJson(response), { challenge: "hello" });
});

test("Tencent SCF entry rejects non-POST requests", async () => {
  const response = await main_handler({ httpMethod: "GET", body: "" });
  assert.equal(response.statusCode, 405);
  assert.deepEqual(bodyJson(response), { ok: false, error: "POST required" });
});

test("Tencent SCF entry handles invalid JSON body", async () => {
  const response = await main_handler({ httpMethod: "POST", body: "{" });
  assert.equal(response.statusCode, 400);
  assert.deepEqual(bodyJson(response), { ok: false, error: "Invalid JSON body" });
});
