#!/usr/bin/env node
/** Pure identity and asset checks for a detail API response owned by one page. */
import { createHash } from "node:crypto";

const DETAIL_PATH = /\/aweme\/v\d+\/web\/aweme\/detail\/?$/i;

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

function urlsFrom(value) {
  const rows = Array.isArray(value) ? value : [value];
  return rows.map(text).filter((value) => /^https?:\/\//i.test(value));
}

function urlList(node) {
  if (!node || typeof node !== "object") return [];
  return urlsFrom(node.url_list || node.urlList || node.url);
}

function detailFrom(payload) {
  if (!payload || typeof payload !== "object") return null;
  const direct = payload.aweme_detail || payload.aweme_info || payload.item;
  if (direct && typeof direct === "object") return direct;
  const nested = payload.data?.aweme_detail || payload.data?.aweme_info || payload.data?.item;
  return nested && typeof nested === "object" ? nested : null;
}

function collectVideoUrls(detail) {
  const video = detail?.video;
  if (!video || typeof video !== "object") return [];
  const rows = [video.play_addr, video.play_addr_264, video.play_addr_h264];
  for (const bitrate of video.bit_rate || video.bit_rate_list || []) {
    rows.push(bitrate?.play_addr);
  }
  return [...new Set(rows.flatMap(urlList))];
}

function collectAudioUrls(detail, { allowMusic = false } = {}) {
  const audio = detail?.audio;
  const rows = [audio?.play_url, audio?.play_addr, detail?.video?.audio?.play_url];
  if (allowMusic) rows.push(detail?.music?.play_url, detail?.music?.play_addr);
  return [...new Set(rows.flatMap(urlList))];
}

function responseBodyText(body, base64Encoded = false) {
  if (typeof body !== "string") return "";
  if (!base64Encoded) return body;
  return Buffer.from(body, "base64").toString("utf8");
}

export function awemeIdFromUrl(value) {
  try {
    const parsed = new URL(value);
    const match = parsed.pathname.match(/\/video\/(\d+)(?:\/|$)/);
    return match?.[1] || "";
  } catch {
    return "";
  }
}

/**
 * Resolve only assets named by the exact detail response and observed by this page.
 * Raw response bodies are hashed for diagnosis and never returned or persisted.
 */
export function resolveOwnedMedia({
  requestedId,
  landedUrl,
  requestUrl,
  responseUrl,
  httpStatus = 0,
  mimeType = "",
  body = "",
  base64Encoded = false,
  observedResources = [],
}) {
  const requested = String(requestedId || "");
  const landedId = awemeIdFromUrl(landedUrl);
  const requestId = (() => {
    try {
      const parsed = new URL(requestUrl);
      if (!DETAIL_PATH.test(parsed.pathname)) return "";
      return parsed.searchParams.get("aweme_id") || parsed.searchParams.get("item_id") || "";
    } catch {
      return "";
    }
  })();
  const rawBody = responseBodyText(body, base64Encoded);
  const bodyHash = createHash("sha256").update(rawBody, "utf8").digest("hex");
  let payload = null;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    payload = null;
  }
  const detail = detailFrom(payload);
  const responseId = String(detail?.aweme_id || detail?.item_id || "");
  const ownership = {
    status: "response_media_identity_unverified",
    requested_id: requested,
    landed_id: landedId,
    response_id: responseId,
    request_id: requestId,
    endpoint: (() => {
      try { return new URL(responseUrl || requestUrl).pathname; } catch { return ""; }
    })(),
    http_status: Number(httpStatus) || 0,
    mime_type: String(mimeType || ""),
    response_body_sha256: bodyHash,
  };
  const result = {
    media_resolution_status: "media_not_extracted",
    item_type: "unknown",
    playable_url: "",
    audio_url: "",
    media_ownership: ownership,
  };

  if (!requestId) return result;
  if (!requested || !landedId || landedId !== requested || requestId !== requested) {
    result.media_resolution_status = "identity_conflict";
    ownership.status = "response_media_identity_conflict";
    return result;
  }
  if (Number(httpStatus) === 404) {
    result.media_resolution_status = "source_not_found";
    return result;
  }
  if ([401, 403, 451].includes(Number(httpStatus))) {
    result.media_resolution_status = "access_restricted";
    return result;
  }
  if (!rawBody) return result;
  if (!detail || !responseId) return result;
  if (responseId !== requested) {
    result.media_resolution_status = "identity_conflict";
    ownership.status = "response_media_identity_conflict";
    return result;
  }

  ownership.status = "response_media_identity_verified";
  const videoUrls = collectVideoUrls(detail);
  const audioUrls = collectAudioUrls(detail, { allowMusic: videoUrls.length > 0 });
  const observed = new Set(urlsFrom(observedResources));
  const matchedVideo = videoUrls.find((url) => observed.has(url)) || "";
  const matchedAudio = audioUrls.find((url) => observed.has(url)) || "";
  const images = detail.image_post_info?.images || detail.image_post?.images || detail.images || [];
  const articleMarker = detail.article_info || detail.article_url || detail.article_content
    || detail.content_type === "article";

  if (matchedVideo) {
    result.media_resolution_status = "resolved";
    result.item_type = "video";
    result.playable_url = matchedVideo;
    result.audio_url = matchedAudio;
  } else if (!videoUrls.length && matchedAudio && detail.audio) {
    result.media_resolution_status = "audio_only";
    result.item_type = "audio_only";
    result.audio_url = matchedAudio;
  } else if (Array.isArray(images) && images.length) {
    result.media_resolution_status = "non_video_image_text";
    result.item_type = "image_text";
  } else if (articleMarker) {
    result.media_resolution_status = "non_video_article";
    result.item_type = "article";
  } else if (payload && Number(payload.status_code) !== 0) {
    const message = text(payload.status_msg || payload.message);
    if (/不存在|已删除|not found/i.test(message)) {
      result.media_resolution_status = "source_not_found";
    } else if (/权限|私密|限制|forbidden|private|restricted/i.test(message)) {
      result.media_resolution_status = "access_restricted";
    }
  }
  return result;
}
