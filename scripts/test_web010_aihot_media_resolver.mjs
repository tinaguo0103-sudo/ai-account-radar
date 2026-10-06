import assert from "node:assert/strict";
import test from "node:test";
import { resolveOwnedMedia } from "./douyin_video_media_identity.mjs";

const requestedId = "12345678901";
const detailUrl = `https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=${requestedId}`;
const sourceUrl = `https://www.douyin.com/video/${requestedId}`;
const videoUrl = "https://v3-web.douyinvod.com/video/tos/media-video-example.mp4";
const audioUrl = "https://v3-web.douyinvod.com/video/tos/media-audio-example.mp4";

function body(detail, extra = {}) {
  return JSON.stringify({ status_code: 0, aweme_detail: detail, ...extra });
}

test("only the exact response-owned and page-observed video/audio assets resolve", () => {
  const result = resolveOwnedMedia({
    requestedId,
    landedUrl: sourceUrl,
    requestUrl: detailUrl,
    responseUrl: detailUrl,
    httpStatus: 200,
    mimeType: "application/json",
    body: body({
      aweme_id: requestedId,
      video: { play_addr: { url_list: [videoUrl] } },
      music: { play_url: { url_list: [audioUrl] } },
    }),
    observedResources: [videoUrl, audioUrl],
  });
  assert.equal(result.media_resolution_status, "resolved");
  assert.equal(result.item_type, "video");
  assert.equal(result.playable_url, videoUrl);
  assert.equal(result.audio_url, audioUrl);
  assert.equal(result.media_ownership.status, "response_media_identity_verified");
  assert.equal(result.media_ownership.response_id, requestedId);
  assert.match(result.media_ownership.response_body_sha256, /^[0-9a-f]{64}$/);
});

test("wrong response identity is retained as a conflict and never returns assets", () => {
  const result = resolveOwnedMedia({
    requestedId,
    landedUrl: sourceUrl,
    requestUrl: detailUrl,
    responseUrl: detailUrl,
    httpStatus: 200,
    body: body({ aweme_id: "10987654321", video: { play_addr: { url_list: [videoUrl] } } }),
    observedResources: [videoUrl],
  });
  assert.equal(result.media_resolution_status, "identity_conflict");
  assert.equal(result.media_ownership.status, "response_media_identity_conflict");
  assert.equal(result.media_ownership.response_id, "10987654321");
  assert.equal(result.playable_url, "");
});

test("asset metadata without a same-page network observation cannot be downloaded", () => {
  const result = resolveOwnedMedia({
    requestedId,
    landedUrl: sourceUrl,
    requestUrl: detailUrl,
    responseUrl: detailUrl,
    httpStatus: 200,
    body: body({ aweme_id: requestedId, video: { play_addr: { url_list: [videoUrl] } } }),
    observedResources: ["https://other.invalid/video.mp4"],
  });
  assert.equal(result.media_resolution_status, "media_not_extracted");
  assert.equal(result.media_ownership.status, "response_media_identity_verified");
  assert.equal(result.playable_url, "");
});

test("typed image-text and audio-only responses do not become video packages", () => {
  const image = resolveOwnedMedia({
    requestedId, landedUrl: sourceUrl, requestUrl: detailUrl, responseUrl: detailUrl,
    httpStatus: 200,
    body: body({ aweme_id: requestedId, image_post_info: { images: [{ uri: "image-1" }] } }),
  });
  assert.equal(image.media_resolution_status, "non_video_image_text");
  assert.equal(image.item_type, "image_text");
  assert.equal(image.playable_url, "");

  const audio = resolveOwnedMedia({
    requestedId, landedUrl: sourceUrl, requestUrl: detailUrl, responseUrl: detailUrl,
    httpStatus: 200,
    body: body({ aweme_id: requestedId, audio: { play_url: { url_list: [audioUrl] } } }),
    observedResources: [audioUrl],
  });
  assert.equal(audio.media_resolution_status, "audio_only");
  assert.equal(audio.item_type, "audio_only");
  assert.equal(audio.audio_url, audioUrl);
  assert.equal(audio.playable_url, "");
});

test("missing ownership response and wrong landing identity fail closed", () => {
  const missing = resolveOwnedMedia({ requestedId, landedUrl: sourceUrl });
  assert.equal(missing.media_resolution_status, "media_not_extracted");
  assert.equal(missing.playable_url, "");

  const wrongLanding = resolveOwnedMedia({
    requestedId, landedUrl: "https://www.douyin.com/video/11111111111",
    requestUrl: detailUrl, responseUrl: detailUrl, httpStatus: 200,
    body: body({ aweme_id: requestedId, video: { play_addr: { url_list: [videoUrl] } } }),
    observedResources: [videoUrl],
  });
  assert.equal(wrongLanding.media_resolution_status, "identity_conflict");
  assert.equal(wrongLanding.playable_url, "");
});
