import { handlePayload } from "../src/receiver.js";

async function readJson(req) {
  if (req.body && typeof req.body === "object" && !Buffer.isBuffer(req.body)) {
    return req.body;
  }
  if (typeof req.body === "string") {
    return JSON.parse(req.body || "{}");
  }
  if (Buffer.isBuffer(req.body)) {
    return JSON.parse(req.body.toString("utf8") || "{}");
  }

  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

async function sendWebResponse(res, response) {
  res.statusCode = response.status;
  for (const [key, value] of response.headers) {
    res.setHeader(key, value);
  }
  res.end(Buffer.from(await response.arrayBuffer()));
}

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(405).json({ ok: false, error: "POST required" });
    return;
  }

  try {
    const payload = await readJson(req);
    const response = await handlePayload(payload, process.env);
    await sendWebResponse(res, response);
  } catch (error) {
    console.error(error);
    res.status(400).json({ ok: false, error: error.message || String(error) });
  }
}
