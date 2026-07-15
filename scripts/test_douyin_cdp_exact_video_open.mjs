import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { canonicalVideoUrl, classifyPage, verifyExpectedTitle, videoIdFromUrl } from "./douyin_cdp_exact_video_open.mjs";

test("accepts only an exact concrete Douyin video URL", () => {
  assert.equal(videoIdFromUrl("https://www.douyin.com/video/7652621198332808488"), "7652621198332808488");
  assert.equal(canonicalVideoUrl("https://www.douyin.com/video/7652621198332808488?x=1"), "https://www.douyin.com/video/7652621198332808488");
  assert.throws(() => videoIdFromUrl("https://www.douyin.com/user/MS4wLjAB"), /not_exact_video_url/);
  assert.throws(() => videoIdFromUrl("https://example.com/video/7652621198332808488"), /not_douyin_url/);
});

test("declares one dedicated primary adapter without failover", () => {
  const source = fs.readFileSync(new URL("./douyin_cdp_exact_video_open.mjs", import.meta.url), "utf8");
  assert.match(source, /primary_adapter: "douyin_cdp_exact_video_v1"/);
  assert.match(source, /attempted_adapters: \["douyin_cdp_exact_video_v1"\]/);
});

test("rejects redirect identity mismatch and verification pages", () => {
  assert.deepEqual(classifyPage("11111111111", {
    final_url: "https://www.douyin.com/video/22222222222", verification_state: "clear",
    exact_title: "title", visible_text: "body",
  }), { status: "source_open_failed", reason: "video_id_identity_mismatch" });
  assert.deepEqual(classifyPage("11111111111", {
    final_url: "https://www.douyin.com/video/11111111111", verification_state: "needs_login",
    exact_title: "title", visible_text: "body",
  }), { status: "source_open_failed", reason: "needs_login" });
});

test("marks metadata-only pages partial, not opened", () => {
  assert.deepEqual(classifyPage("11111111111", {
    final_url: "https://www.douyin.com/video/11111111111", verification_state: "clear",
    exact_title: "", visible_text: "",
  }), { status: "opened_partial", reason: "visible_content_insufficient" });
});

test("rejects an unavailable exact video without treating recommendation text as evidence", () => {
  assert.deepEqual(classifyPage("11111111111", {
    final_url: "https://www.douyin.com/video/11111111111", verification_state: "clear",
    page_state: "video_unavailable", exact_title: "recommended item", visible_text: "你要观看的视频不存在",
  }), { status: "source_open_failed", reason: "video_unavailable" });
});

test("separates a verified visible title prefix from caption body", () => {
  assert.deepEqual(
    verifyExpectedTitle("Codex联动Obsidian，搭建超强知识库，手把手教程", "Codex联动Obsidian，搭建超强知识库，手把手教程 用Codex搭建自生长知识库"),
    { verified: true, exact_title: "Codex联动Obsidian，搭建超强知识库，手把手教程", caption_body: "用Codex搭建自生长知识库" },
  );
  assert.equal(verifyExpectedTitle("unrelated", "visible source").verified, false);
  assert.equal(verifyExpectedTitle("Codex + Obsidian 搭知识库", "Codex+Obsidian搭知识库 后续正文").verified, true);
  assert.equal(verifyExpectedTitle("多宫格故事板2.0，出视频比你想的还简单🎬", "第20集 | 多宫格故事板2.0，出视频比你想的还简单 后续正文").verified, true);
});
