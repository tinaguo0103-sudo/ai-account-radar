#!/usr/bin/env node
/** Capture Feishu card pages with DOM and viewport identity sidecars over trusted Chrome CDP. */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

function parseArgs(argv = process.argv.slice(2)) {
  const options = { cdp: "http://127.0.0.1:9227", manifest: "", outDir: "", marker: "", pageIndex: 0 };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--cdp") options.cdp = argv[++i];
    else if (arg === "--manifest") options.manifest = argv[++i];
    else if (arg === "--out-dir") options.outDir = argv[++i];
    else if (arg === "--marker") options.marker = argv[++i];
    else if (arg === "--page-index") options.pageIndex = Number(argv[++i]);
    else throw new Error(`unknown_argument:${arg}`);
  }
  if (!options.manifest || !options.outDir || !options.marker) throw new Error("--manifest, --out-dir and --marker are required");
  return options;
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

class CdpClient {
  constructor(wsUrl) { this.wsUrl = wsUrl; this.seq = 0; this.pending = new Map(); }
  async open() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("cdp_websocket_timeout")), 10000);
      this.ws.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
      this.ws.addEventListener("error", () => { clearTimeout(timer); reject(new Error("cdp_websocket_error")); }, { once: true });
    });
    this.ws.addEventListener("message", event => {
      const payload = JSON.parse(event.data);
      if (!payload.id || !this.pending.has(payload.id)) return;
      const pending = this.pending.get(payload.id); this.pending.delete(payload.id);
      payload.error ? pending.reject(new Error(payload.error.message)) : pending.resolve(payload.result);
    });
  }
  send(method, params = {}) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  close() { this.ws?.close(); }
}

const sha256 = value => crypto.createHash("sha256").update(value).digest("hex");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function evaluate(client, expression) {
  const result = await client.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) {
    const detail = result.exceptionDetails.exception?.description || result.exceptionDetails.text || "runtime_evaluate_failed";
    throw new Error(detail);
  }
  return result.result.value;
}

async function locateCardPage(client, marker, pageIndex, pageCount) {
  const wanted = `第 ${pageIndex}/${pageCount} 页`;
  for (let attempt = 0; attempt < 24; attempt += 1) {
    const result = await evaluate(client, `(() => {
      const marker = ${JSON.stringify(marker)};
      const wanted = ${JSON.stringify(wanted)};
      const desired = ${pageIndex};
      const roots = [...document.querySelectorAll('.universal-card-root')];
      const found = roots.findIndex(root => root.innerText.includes(marker) && root.innerText.includes(wanted));
      if (found >= 0) return { found };
      const current = roots.find(root => root.innerText.includes(marker));
      const match = current?.innerText.match(/第\s*(\d+)/);
      const currentPage = match ? Number(match[1]) : ${pageCount};
      let scroll = current;
      while (scroll && !(scroll.scrollHeight > scroll.clientHeight + 40)) scroll = scroll.parentElement;
      if (!scroll) {
        scroll = [...document.querySelectorAll('div')]
          .filter(node => node.scrollHeight > node.clientHeight + 200)
          .sort((a, b) => b.clientHeight - a.clientHeight)[0];
      }
      if (!scroll) return { found: -1, moved: false, currentPage };
      const direction = desired < currentPage ? -1 : 1;
      scroll.scrollTop += direction * Math.max(500, scroll.clientHeight * 0.8);
      return { found: -1, moved: true, currentPage, scrollTop: scroll.scrollTop };
    })()`);
    if (result.found >= 0) return result.found;
    if (!result.moved) break;
    await sleep(600);
  }
  return -1;
}

async function main() {
  const options = parseArgs();
  const manifest = JSON.parse(fs.readFileSync(options.manifest, "utf8"));
  fs.mkdirSync(options.outDir, { recursive: true });
  const targets = await getJson(`${options.cdp}/json`);
  const target = targets.find(item => item.type === "page" && /feishu\.cn|larksuite\.com/.test(item.url || ""));
  if (!target) throw new Error("trusted_feishu_page_not_found");
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.open();
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Page.reload", { ignoreCache: true });
  await sleep(5000);
  const evidence = [];
  try {
    const requestedPages = options.pageIndex
      ? manifest.pages.filter(page => page.page === options.pageIndex)
      : manifest.pages;
    if (!requestedPages.length) throw new Error(`manifest_page_not_found:${options.pageIndex}`);
    for (const page of requestedPages) {
      await evaluate(client, `(() => {
        const jump = [...document.querySelectorAll('button,div,span')]
          .find(node => /条新消息/.test(node.innerText || '') && node.getBoundingClientRect().width > 0);
        if (jump) {
          (jump.closest('button') || jump).click();
          return true;
        }
        return false;
      })()`);
      await sleep(1000);
      const pageLabel = `第 ${page.page}/${manifest.page_count} 页`;
      const rootIndex = await locateCardPage(client, options.marker, page.page, manifest.page_count);
      if (rootIndex < 0) {
        const diagnostic = await evaluate(client, `(() => ({
          roots: [...document.querySelectorAll('.universal-card-root')].map(root => root.innerText.slice(0, 300)),
          scrollables: [...document.querySelectorAll('div')].filter(node => node.scrollHeight > node.clientHeight + 200)
            .map(node => ({className: node.className, scrollTop: node.scrollTop, clientHeight: node.clientHeight, scrollHeight: node.scrollHeight}))
            .sort((a, b) => b.clientHeight - a.clientHeight).slice(0, 12)
        }))()`);
        fs.writeFileSync(path.join(options.outDir, "capture_debug.json"), JSON.stringify(diagnostic, null, 2));
        throw new Error(`card_page_not_mounted:${page.page}`);
      }
      const rootLookup = `[...document.querySelectorAll('.universal-card-root')]
        .filter(root => {
          const rect = root.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && root.innerText.includes(${JSON.stringify(options.marker)}) && root.innerText.includes(${JSON.stringify(pageLabel)});
        })
        .sort((a, b) => {
          const ar = a.getBoundingClientRect();
          const br = b.getBoundingClientRect();
          const ai = Math.max(0, Math.min(ar.bottom, innerHeight) - Math.max(ar.top, 0));
          const bi = Math.max(0, Math.min(br.bottom, innerHeight) - Math.max(br.top, 0));
          return bi - ai;
        })[0]`;
      const captureDom = async () => evaluate(client, `(() => {
        const root = ${rootLookup};
        if (!root) throw new Error('card_root_missing');
        const first = [...root.querySelectorAll('.universal-card-text--bold')][0]?.innerText || '';
        return { text: root.innerText, html: root.outerHTML, firstTitle: first.replace(/^1\\.\\s*/, '') };
      })()`);
      const dom = await captureDom();
      const domTextPath = path.join(options.outDir, `page${page.page}_dom_text.txt`);
      const domHtmlPath = path.join(options.outDir, `page${page.page}_dom.html`);
      fs.writeFileSync(domTextPath, dom.text);
      fs.writeFileSync(domHtmlPath, dom.html);
      const domHash = sha256(dom.text.replace(/\s+/g, " ").trim() + "\n" + dom.html);
      // Feishu can keep an older card composited until the newest message is
      // scrolled once. Warm the latest page without recording evidence.
      await evaluate(client, `(() => {
        const root = ${rootLookup};
        if (!root) throw new Error('card_root_missing_before_warmup');
        root.scrollIntoView({block: 'end', behavior: 'instant'});
        return true;
      })()`);
      await sleep(800);
      await evaluate(client, `(() => {
        const root = ${rootLookup};
        if (!root) throw new Error('card_root_missing_during_warmup');
        root.scrollIntoView({block: 'start', behavior: 'instant'});
        return true;
      })()`);
      await sleep(800);
      for (const position of ["bottom", "top", "bottom"]) {
        const scrollResult = await evaluate(client, `(() => {
          const root = ${rootLookup};
          if (!root) throw new Error('card_root_missing_before_scroll');
          const block = ${JSON.stringify(position === "top" ? "start" : "end")};
          const scrollables = [];
          for (let node = root.parentElement; node; node = node.parentElement) {
            const style = getComputedStyle(node);
            if (node.scrollHeight > node.clientHeight + 20 && /auto|scroll/.test(style.overflowY)) scrollables.push(node);
          }
          root.scrollIntoView({block, behavior: 'instant'});
          for (const node of scrollables) {
            const rootRect = root.getBoundingClientRect();
            const nodeRect = node.getBoundingClientRect();
            const target = block === 'start'
              ? rootRect.top - nodeRect.top + node.scrollTop
              : rootRect.bottom - nodeRect.bottom + node.scrollTop;
            node.scrollTop = target;
          }
          const rect = root.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height, scrollableCount: scrollables.length};
        })()`);
        await sleep(800);
        const viewport = await evaluate(client, `(() => {
          const root = ${rootLookup};
          if (!root) throw new Error('card_root_missing_before_capture');
          const nodes = [...root.querySelectorAll('.universal-card-text, button')];
          const visible = nodes.filter(node => {
            const r = node.getBoundingClientRect();
            return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
          }).map(node => node.innerText).filter(Boolean);
          const first = [...root.querySelectorAll('.universal-card-text--bold')][0]?.innerText || '';
          const rect = root.getBoundingClientRect();
          const ancestors = [];
          for (let node = root; node && ancestors.length < 12; node = node.parentElement) {
            const r = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            ancestors.push({tag: node.tagName, className: String(node.className || ''), top: r.top, bottom: r.bottom, height: r.height, scrollTop: node.scrollTop, scrollHeight: node.scrollHeight, clientHeight: node.clientHeight, transform: style.transform, position: style.position, zIndex: style.zIndex, display: style.display, visibility: style.visibility});
          }
          const hit = document.elementFromPoint(Math.min(window.innerWidth - 10, Math.max(10, rect.left + 20)), Math.min(window.innerHeight - 10, Math.max(10, rect.top + 20)));
          return { text: visible.join('\\n'), firstTitle: first.replace(/^1\\.\\s*/, ''), rect: {top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, width: rect.width, height: rect.height}, rootContainsHit: Boolean(hit && root.contains(hit)), hitText: hit?.innerText?.slice(0, 120) || '', ancestors };
        })()`);
        if (viewport.rect.width <= 0 || viewport.rect.height <= 0 || viewport.rect.bottom <= 0 || viewport.rect.top >= 1240) {
          throw new Error(`card_root_not_visible:${page.page}:${position}:${JSON.stringify({scrollResult, rect: viewport.rect})}`);
        }
        const png = await client.send("Page.captureScreenshot", { format: "png", fromSurface: true });
        const screenshotPath = path.join(options.outDir, `page${page.page}_${position}.png`);
        const bytes = Buffer.from(png.data, "base64");
        fs.writeFileSync(screenshotPath, bytes);
        const captureEvidence = {
          page_index: page.page,
          page_count: manifest.page_count,
          position,
          screenshot_path: screenshotPath,
          screenshot_sha256: sha256(bytes),
          dom_text_path: domTextPath,
          dom_html_path: domHtmlPath,
          dom_snapshot_hash: domHash,
          first_candidate_id: page.first_candidate_id,
          first_candidate_title: page.first_candidate_title,
          viewport_first_candidate_title: position === "top" ? viewport.firstTitle : "",
          viewport_text: viewport.text,
          viewport_rect: viewport.rect,
          root_contains_hit: viewport.rootContainsHit,
          hit_text: viewport.hitText,
          root_ancestors: viewport.ancestors,
          captured_at: new Date().toISOString(),
          browser_surface: "trusted_chrome_cdp",
          target_url: target.url,
        };
        const existing = evidence.findIndex(item => item.page_index === page.page && item.position === position);
        if (existing >= 0) evidence[existing] = captureEvidence;
        else evidence.push(captureEvidence);
      }
    }
  } finally {
    client.close();
  }
  const output = path.join(options.outDir, "screenshot_page_identity_evidence.json");
  fs.writeFileSync(output, JSON.stringify(evidence, null, 2));
  process.stdout.write(JSON.stringify({ ok: true, page_count: manifest.page_count, requested_page_index: options.pageIndex || null, captures: evidence.length, output }) + "\n");
}

main().catch(error => { console.error(error.stack || error.message); process.exit(1); });
