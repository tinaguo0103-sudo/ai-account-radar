import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  persistAccountHealth,
  validateHealthProjection,
} from "./douyin_cdp_source_watch_probe.mjs";

const runId = "run_20260725_210000_ar047qa";
const source = {
  account_name: "QA",
  url: "https://www.douyin.com/user/exact",
  source_id: "douyin:qa",
};
const row = {
  account_name: "QA",
  status: "success",
  extraction_diagnostics: {},
};

function injectedIo(kind) {
  let writes = 0;
  let fsyncs = 0;
  let renames = 0;
  return new Proxy(fs, {
    get(receiver, property) {
      if (property === "writeSync") {
        return (...args) => {
          writes += 1;
          if (writes === 2 && kind === "write") throw new Error("injected_projection_write");
          if (writes === 2 && kind === "partial_write") {
            receiver.writeSync(args[0], "{\"schema_version\":");
            throw new Error("injected_projection_partial_write");
          }
          return receiver.writeSync(...args);
        };
      }
      if (property === "fsyncSync") {
        return (...args) => {
          fsyncs += 1;
          if (kind === "file_fsync" && fsyncs === 3) throw new Error("injected_projection_file_fsync");
          if (kind === "parent_fsync" && fsyncs === 4) throw new Error("injected_projection_parent_fsync");
          return receiver.fsyncSync(...args);
        };
      }
      if (property === "renameSync") {
        return (...args) => {
          renames += 1;
          if (kind === "rename" && renames === 2) throw new Error("injected_projection_rename");
          return receiver.renameSync(...args);
        };
      }
      return receiver[property];
    },
  });
}

for (const existing of [false, true]) {
  for (const kind of ["write", "partial_write", "file_fsync", "rename", "parent_fsync"]) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), `ar047-projection-${kind}-`));
    const ledger = path.join(root, "durable.json");
    const projectionPath = path.join(root, "run", "account_health.json");
    fs.mkdirSync(path.dirname(projectionPath), { recursive: true });
    const previous = `${JSON.stringify({ schema_version: 1, marker: "old-projection" }, null, 2)}\n`;
    if (existing) fs.writeFileSync(projectionPath, previous);

    const result = persistAccountHealth(
      ledger,
      projectionPath,
      [source],
      [row],
      runId,
      "2026-07-25T21:00:00Z",
      injectedIo(kind),
    );
    assert.equal(result.authority.ok, true);
    assert.equal(result.projection.ok, false);
    assert.equal(result.projection.status, "health_projection_write_failed");
    const durable = JSON.parse(fs.readFileSync(ledger, "utf8"));
    assert.ok(durable.events[`${runId}|douyin:qa`]);
    assert.equal(
      existing ? fs.readFileSync(projectionPath, "utf8") : fs.existsSync(projectionPath),
      existing ? previous : false,
    );
    const residue = fs.readdirSync(path.dirname(projectionPath))
      .filter((name) => name.includes(".tmp") || name.includes(".restore"));
    assert.deepEqual(residue, []);
  }
}

{
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ar047-projection-retry-"));
  const ledger = path.join(root, "durable.json");
  const projectionPath = path.join(root, "run", "account_health.json");
  const failed = persistAccountHealth(
    ledger,
    projectionPath,
    [source],
    [row],
    runId,
    "2026-07-25T21:00:00Z",
    injectedIo("write"),
  );
  assert.equal(failed.projection.ok, false);
  const durableBefore = JSON.parse(fs.readFileSync(ledger, "utf8"));
  const eventCount = Object.keys(durableBefore.events).length;
  const counters = durableBefore.accounts.map((item) => ({
    source_id: item.source_id,
    consecutive_failures: item.consecutive_failures,
    current_outcome: item.current_outcome,
  }));
  const retried = persistAccountHealth(
    ledger,
    projectionPath,
    [source],
    [row],
    runId,
    "2026-07-25T21:00:00Z",
  );
  assert.equal(retried.projection.ok, true);
  const durableAfter = JSON.parse(fs.readFileSync(ledger, "utf8"));
  assert.equal(Object.keys(durableAfter.events).length, eventCount);
  assert.deepEqual(durableAfter.accounts.map((item) => ({
    source_id: item.source_id,
    consecutive_failures: item.consecutive_failures,
    current_outcome: item.current_outcome,
  })), counters);
  const projection = JSON.parse(fs.readFileSync(projectionPath, "utf8"));
  const durableBytes = fs.readFileSync(ledger);
  assert.equal(validateHealthProjection(projection, ledger, durableBytes, runId).ok, true);
  projection.authority.sha256 = "stale";
  assert.equal(validateHealthProjection(projection, ledger, durableBytes, runId).ok, false);
}

console.log(JSON.stringify({
  ok: true,
  projection_failures: 10,
  retry_rebuilt: true,
  stale_digest_rejected: true,
}));
