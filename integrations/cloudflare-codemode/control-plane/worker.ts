export interface Env {
  YUKI_ASGI_ORIGIN: string;
  YUKI_ASGI_ACCESS_CLIENT_ID: string;
  YUKI_ASGI_ACCESS_CLIENT_SECRET: string;
  CODEMODE_ADAPTER_TOKEN: string;
}

const MAX_REQUEST_BYTES = 96 * 1024;
const MAX_RESPONSE_BYTES = 96 * 1024;
const UPSTREAM_TIMEOUT_MS = 10_000;
const ALLOWED_PATHS = new Set([
  "/v1/internal/executions/authorize",
  "/v1/internal/executions/tool-call",
  "/v1/internal/executions/complete",
]);

function json(status: number, payload: Record<string, unknown>): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

function constantTimeEqual(actual: string, expected: string): boolean {
  const length = Math.max(actual.length, expected.length);
  let difference = actual.length ^ expected.length;
  for (let index = 0; index < length; index += 1) {
    difference |= (actual.charCodeAt(index) || 0) ^ (expected.charCodeAt(index) || 0);
  }
  return difference === 0;
}

function configuredOrigin(value: string): URL | null {
  try {
    const origin = new URL(value);
    if (
      origin.protocol !== "https:" ||
      origin.username !== "" ||
      origin.password !== "" ||
      origin.pathname !== "/" ||
      origin.search !== "" ||
      origin.hash !== ""
    ) return null;
    return origin;
  } catch {
    return null;
  }
}

async function readBounded(stream: ReadableStream<Uint8Array> | null, limit: number): Promise<Uint8Array | null> {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output;
}

export function createControlPlaneHandler(
  env: Env,
  fetchOrigin: typeof fetch = fetch,
): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST") return json(405, { error: "method_not_allowed" });

    const requestUrl = new URL(request.url);
    const path = requestUrl.pathname;
    if (!ALLOWED_PATHS.has(path) || requestUrl.search !== "") return json(404, { error: "not_found" });

    const contentType = request.headers.get("content-type") ?? "";
    if (contentType.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
      return json(415, { error: "unsupported_media_type" });
    }

    const adapterToken = request.headers.get("x-yuki-adapter-token") ?? "";
    if (
      typeof env.CODEMODE_ADAPTER_TOKEN !== "string" ||
      env.CODEMODE_ADAPTER_TOKEN.length < 32 ||
      !constantTimeEqual(adapterToken, env.CODEMODE_ADAPTER_TOKEN)
    ) return json(401, { error: "adapter_authentication_required" });

    const origin = configuredOrigin(env.YUKI_ASGI_ORIGIN);
    if (
      !origin ||
      typeof env.YUKI_ASGI_ACCESS_CLIENT_ID !== "string" ||
      env.YUKI_ASGI_ACCESS_CLIENT_ID.length < 16 ||
      typeof env.YUKI_ASGI_ACCESS_CLIENT_SECRET !== "string" ||
      env.YUKI_ASGI_ACCESS_CLIENT_SECRET.length < 32
    ) return json(503, { error: "control_plane_not_configured" });

    const body = await readBounded(request.body, MAX_REQUEST_BYTES);
    if (body === null) return json(413, { error: "request_too_large" });

    const target = new URL(path, origin);
    let upstream: Response;
    try {
      upstream = await fetchOrigin(target, {
        method: "POST",
        redirect: "manual",
        headers: {
          "content-type": "application/json",
          "x-yuki-adapter-token": env.CODEMODE_ADAPTER_TOKEN,
          "cf-access-client-id": env.YUKI_ASGI_ACCESS_CLIENT_ID,
          "cf-access-client-secret": env.YUKI_ASGI_ACCESS_CLIENT_SECRET,
        },
        body,
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      });
    } catch {
      return json(502, { error: "control_plane_unreachable" });
    }

    if (upstream.status >= 300 && upstream.status < 400) {
      await upstream.body?.cancel();
      return json(502, { error: "control_plane_redirect_rejected" });
    }
    const responseBody = await readBounded(upstream.body, MAX_RESPONSE_BYTES);
    if (responseBody === null) return json(502, { error: "control_plane_response_too_large" });
    const responseType = upstream.headers.get("content-type") ?? "";
    if (responseType.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
      return json(502, { error: "invalid_control_plane_response" });
    }
    try {
      JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(responseBody));
    } catch {
      return json(502, { error: "invalid_control_plane_response" });
    }
    return new Response(responseBody, {
      status: upstream.status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
      },
    });
  };
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return createControlPlaneHandler(env)(request);
  },
};
