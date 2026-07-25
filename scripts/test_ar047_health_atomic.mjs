import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { atomicWriteJson } from "./douyin_cdp_source_watch_probe.mjs";

const operations = ["writeSync", "fsyncSync", "renameSync"];

for (const operation of operations) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), `ar047-${operation}-`));
  const target = path.join(root, "health.json");
  const original = `${JSON.stringify({ schema_version: 1, marker: "original" }, null, 2)}\n`;
  fs.writeFileSync(target, original);
  let injected = false;
  const io = new Proxy(fs, {
    get(receiver, property) {
      if (property === operation) {
        return (...args) => {
          if (!injected) {
            injected = true;
            throw new Error(`injected_${operation}`);
          }
          return receiver[property](...args);
        };
      }
      return receiver[property];
    },
  });
  assert.throws(() => atomicWriteJson(target, { schema_version: 1, marker: "new" }, io));
  assert.equal(fs.readFileSync(target, "utf8"), original, `${operation} must preserve original`);
  assert.doesNotThrow(() => JSON.parse(fs.readFileSync(target, "utf8")));
  assert.deepEqual(fs.readdirSync(root), ["health.json"], `${operation} must clean temporary files`);
}

{
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ar047-parent-fsync-"));
  const target = path.join(root, "health.json");
  const original = `${JSON.stringify({ schema_version: 1, marker: "original" }, null, 2)}\n`;
  fs.writeFileSync(target, original);
  let fsyncCalls = 0;
  const io = new Proxy(fs, {
    get(receiver, property) {
      if (property === "fsyncSync") {
        return (...args) => {
          fsyncCalls += 1;
          if (fsyncCalls === 2) throw new Error("injected_parent_fsync");
          return receiver.fsyncSync(...args);
        };
      }
      return receiver[property];
    },
  });
  assert.throws(() => atomicWriteJson(target, { schema_version: 1, marker: "new" }, io));
  assert.equal(fs.readFileSync(target, "utf8"), original);
  assert.doesNotThrow(() => JSON.parse(fs.readFileSync(target, "utf8")));
  assert.deepEqual(fs.readdirSync(root), ["health.json"]);
}

{
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ar047-first-write-"));
  const target = path.join(root, "health.json");
  const io = new Proxy(fs, {
    get(receiver, property) {
      if (property === "writeSync") return () => { throw new Error("injected_first_write"); };
      return receiver[property];
    },
  });
  assert.throws(() => atomicWriteJson(target, { schema_version: 1 }, io));
  assert.equal(fs.existsSync(target), false);
  assert.deepEqual(fs.readdirSync(root), []);
}

{
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ar047-partial-write-"));
  const target = path.join(root, "health.json");
  const original = `${JSON.stringify({ schema_version: 1, marker: "original" }, null, 2)}\n`;
  fs.writeFileSync(target, original);
  const io = new Proxy(fs, {
    get(receiver, property) {
      if (property === "writeSync") {
        return (fd) => {
          receiver.writeSync(fd, "{\"schema_version\":");
          throw new Error("injected_partial_write");
        };
      }
      return receiver[property];
    },
  });
  assert.throws(() => atomicWriteJson(target, { schema_version: 1, marker: "new" }, io));
  assert.equal(fs.readFileSync(target, "utf8"), original);
  assert.doesNotThrow(() => JSON.parse(fs.readFileSync(target, "utf8")));
  assert.deepEqual(fs.readdirSync(root), ["health.json"]);
}

console.log(JSON.stringify({
  ok: true,
  injected_operations: [...operations, "parent_fsync", "first_write", "partial_write"],
  temp_files: 0,
}));
