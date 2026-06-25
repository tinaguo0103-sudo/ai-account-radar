import { handleRequest } from "../src/receiver.js";

export const config = {
  runtime: "edge",
  regions: ["hnd1", "sin1"],
};

export default async function handler(request) {
  return handleRequest(request, process.env);
}
