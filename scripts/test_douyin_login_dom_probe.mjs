#!/usr/bin/env node
import assert from "node:assert/strict";
import { classifyLoginMarkers } from "./douyin_login_dom_probe.mjs";

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
console.log("douyin login DOM classifier tests passed");
