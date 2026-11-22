import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";

const source = readFileSync(
  new URL("../../ui/web/chat-api.js", import.meta.url),
  "utf8",
);

function loadApi(fetchImpl) {
  const context = { window: {}, fetch: fetchImpl, Headers };
  runInNewContext(source, context);
  return context.window.YukiApi;
}

test("API client sends bearer auth and preserves caller headers", async () => {
  let request;
  const api = loadApi(async (path, options) => {
    request = { path, options };
    return { status: 200, ok: true, json: async () => ({ accepted: true }) };
  });

  const result = await api.request(
    "/v1/tasks",
    { method: "POST", headers: { "X-Request-ID": "request-1" }, body: "{}" },
    "secret-token",
  );

  assert.deepEqual(result, { accepted: true });
  assert.equal(request.path, "/v1/tasks");
  assert.equal(request.options.headers.get("Authorization"), "Bearer secret-token");
  assert.equal(request.options.headers.get("Content-Type"), "application/json");
  assert.equal(request.options.headers.get("X-Request-ID"), "request-1");
});

test("API client handles 204 without attempting JSON parsing", async () => {
  const api = loadApi(async () => ({
    status: 204,
    ok: true,
    json: async () => { throw new Error("204 response must not be parsed"); },
  }));

  const result = await api.request("/v1/logout");
  assert.equal(Object.keys(result).length, 0);
});

test("API client invokes unauthorized handler and surfaces server error", async () => {
  let signedOut = false;
  const api = loadApi(async () => ({
    status: 401,
    ok: false,
    json: async () => ({ error: "session_expired" }),
  }));

  await assert.rejects(
    api.request("/v1/private", {}, "expired-token", () => { signedOut = true; }),
    /session_expired/,
  );
  assert.equal(signedOut, true);
});

test("API client uses a safe fallback for malformed error payloads", async () => {
  const api = loadApi(async () => ({
    status: 503,
    ok: false,
    json: async () => null,
  }));

  await assert.rejects(api.request("/v1/private"), /Request failed \(503\)/);
});
