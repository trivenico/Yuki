import { describe, expect, it, vi } from "vitest";
import { createControlPlaneHandler, type Env } from "./worker";

const env: Env = {
  YUKI_ASGI_ORIGIN: "https://yuki-origin.example",
  YUKI_ASGI_ACCESS_CLIENT_ID: "access-client-id-1234567890",
  YUKI_ASGI_ACCESS_CLIENT_SECRET: "access-client-secret-with-at-least-32-bytes",
  CODEMODE_ADAPTER_TOKEN: "adapter-token-with-at-least-32-bytes",
};

function request(
  path = "/v1/internal/executions/authorize",
  init: RequestInit = {},
): Request {
  return new Request(`https://control-plane.internal${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-yuki-adapter-token": env.CODEMODE_ADAPTER_TOKEN,
    },
    body: "{}",
    ...init,
  });
}

describe("private Cloudflare control-plane forwarder", () => {
  it("forwards only the allowlisted RPC to the fixed HTTPS origin with Access credentials", async () => {
    const fetchOrigin = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ authorized: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const handler = createControlPlaneHandler(env, fetchOrigin as typeof fetch);
    const response = await handler(request());

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ authorized: true });
    expect(fetchOrigin).toHaveBeenCalledOnce();
    const [target, init] = fetchOrigin.mock.calls[0];
    expect(String(target)).toBe("https://yuki-origin.example/v1/internal/executions/authorize");
    const headers = new Headers(init?.headers);
    expect(headers.get("cf-access-client-id")).toBe(env.YUKI_ASGI_ACCESS_CLIENT_ID);
    expect(headers.get("cf-access-client-secret")).toBe(env.YUKI_ASGI_ACCESS_CLIENT_SECRET);
    expect(headers.get("x-yuki-adapter-token")).toBe(env.CODEMODE_ADAPTER_TOKEN);
    expect(init?.redirect).toBe("manual");
  });

  it("rejects non-allowlisted paths before contacting the origin", async () => {
    const fetchOrigin = vi.fn();
    const handler = createControlPlaneHandler(env, fetchOrigin as typeof fetch);
    const response = await handler(request("/admin"));
    expect(response.status).toBe(404);
    expect(fetchOrigin).not.toHaveBeenCalled();
  });

  it("rejects requests without the adapter secret", async () => {
    const fetchOrigin = vi.fn();
    const handler = createControlPlaneHandler(env, fetchOrigin as typeof fetch);
    const response = await handler(request("/v1/internal/executions/authorize", {
      headers: { "content-type": "application/json", "x-yuki-adapter-token": "wrong" },
    }));
    expect(response.status).toBe(401);
    expect(fetchOrigin).not.toHaveBeenCalled();
  });

  it("fails closed when required secrets are missing", async () => {
    const fetchOrigin = vi.fn();
    const noAdapterSecret = createControlPlaneHandler({
      ...env,
      CODEMODE_ADAPTER_TOKEN: undefined as unknown as string,
    }, fetchOrigin as typeof fetch);
    expect((await noAdapterSecret(request())).status).toBe(401);

    const noAccessSecret = createControlPlaneHandler({
      ...env,
      YUKI_ASGI_ACCESS_CLIENT_SECRET: undefined as unknown as string,
    }, fetchOrigin as typeof fetch);
    expect((await noAccessSecret(request())).status).toBe(503);
    expect(fetchOrigin).not.toHaveBeenCalled();
  });

  it("rejects untrusted HTTP origins and URLs containing credentials or paths", async () => {
    const fetchOrigin = vi.fn();
    const handler = createControlPlaneHandler({
      ...env,
      YUKI_ASGI_ORIGIN: "http://yuki-origin.example",
    }, fetchOrigin as typeof fetch);
    const response = await handler(request());
    expect(response.status).toBe(503);
    expect(fetchOrigin).not.toHaveBeenCalled();
  });

  it("bounds request bodies before forwarding", async () => {
    const fetchOrigin = vi.fn();
    const handler = createControlPlaneHandler(env, fetchOrigin as typeof fetch);
    const response = await handler(request("/v1/internal/executions/authorize", { body: "x".repeat(96 * 1024 + 1) }));
    expect(response.status).toBe(413);
    expect(fetchOrigin).not.toHaveBeenCalled();
  });

  it("rejects redirects and non-JSON upstream responses", async () => {
    const redirect = createControlPlaneHandler(env, vi.fn(async () =>
      new Response(null, { status: 302, headers: { location: "https://evil.example" } }),
    ) as typeof fetch);
    expect((await redirect(request())).status).toBe(502);

    const html = createControlPlaneHandler(env, vi.fn(async () =>
      new Response("<html>no</html>", { status: 200, headers: { "content-type": "text/html" } }),
    ) as typeof fetch);
    expect((await html(request())).status).toBe(502);
  });

  it("fails closed on oversized upstream responses", async () => {
    const handler = createControlPlaneHandler(env, vi.fn(async () =>
      new Response("x".repeat(96 * 1024 + 1), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as typeof fetch);
    const response = await handler(request());
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "control_plane_response_too_large" });
  });
});
