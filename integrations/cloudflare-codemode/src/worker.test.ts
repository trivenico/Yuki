import { describe, expect, it, vi } from "vitest";

vi.mock("@cloudflare/codemode", () => ({
  DynamicWorkerExecutor: class {
    constructor(_options: unknown) {}
    async execute() { return { result: null, logs: [] }; }
  },
}));

import { createExecutionHandler, type Env, type ExecutionEnvelope } from "./worker";

const encoder = new TextEncoder();

async function digest(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return [...new Uint8Array(bytes)].map((part) => part.toString(16).padStart(2, "0")).join("");
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function makeEnvelope(overrides: Partial<ExecutionEnvelope> = {}): Promise<ExecutionEnvelope> {
  const base = {
    execution_id: "exec-001",
    actor_id: "actor-001",
    session_id: "a".repeat(64),
    task_id: "task-001",
    source: "const rows = await workspace.list({}); return rows;",
    tool_names: ["workspace.list"],
    timeout_seconds: 15,
    max_tool_calls: 2,
    max_result_bytes: 4096,
    network_access: false as const,
  };
  const request = { ...base, ...overrides };
  const source_sha256 = await digest(request.source);
  const manifest_sha256 = await digest(canonical({
    execution_id: request.execution_id,
    actor_id: request.actor_id,
    session_id: request.session_id,
    task_id: request.task_id,
    source_sha256,
    tool_names: request.tool_names,
    timeout_seconds: request.timeout_seconds,
    max_tool_calls: request.max_tool_calls,
    max_result_bytes: request.max_result_bytes,
    network_access: false,
  }));
  return { ...request, source_sha256, manifest_sha256, grant: "opaque-one-shot-grant" };
}

function env(controlPlaneFetch: typeof fetch): Env {
  return {
    LOADER: {} as Env["LOADER"],
    CONTROL_PLANE: { fetch: controlPlaneFetch },
    CONTROL_PLANE_TOKEN: "test-adapter-token-with-at-least-32-bytes",
  };
}

describe("Cloudflare Code Mode execution gateway", () => {
  it("authorizes before starting the sandbox and proxies only manifested tools", async () => {
    const envelope = await makeEnvelope();
    const calls: Array<{ path: string; body: Record<string, unknown> }> = [];
    const controlFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input)).pathname;
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      calls.push({ path, body });
      return Response.json(path.endsWith("/tool-call")
        ? { authorized: true, result: { entries: [] } }
        : { authorized: true });
    });
    const execute = vi.fn(async (_source: string, providers: Array<{ name: string; fns: Record<string, (...args: unknown[]) => Promise<unknown>> }>) => {
      const rows = await providers[0].fns.list({});
      return { result: { count: Array.isArray(rows) ? rows.length : 0 }, logs: ["ok"] };
    });
    const handler = createExecutionHandler(env(controlFetch), () => ({ execute } as never));
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    }));

    expect(response.status).toBe(200);
    expect(execute).toHaveBeenCalledOnce();
    expect(calls.map((call) => call.path)).toEqual([
      "/v1/internal/executions/authorize",
      "/v1/internal/executions/tool-call",
      "/v1/internal/executions/complete",
    ]);
    expect(calls[0].body).toMatchObject({
      execution_id: envelope.execution_id,
      source_sha256: envelope.source_sha256,
      manifest_sha256: envelope.manifest_sha256,
      tool_names: envelope.tool_names,
      network_access: false,
    });
    expect(calls[1].body).toMatchObject({
      execution_id: envelope.execution_id,
      source_sha256: envelope.source_sha256,
      manifest_sha256: envelope.manifest_sha256,
      tool_names: envelope.tool_names,
      tool_name: "workspace.list",
      call_index: 0,
    });
    expect(calls[2].body.tool_calls).toBe(1);
  });

  it("does not start generated code when the control plane rejects or replay-detects a grant", async () => {
    const envelope = await makeEnvelope();
    const execute = vi.fn();
    const controlFetch = vi.fn(async () => Response.json({ authorized: false }, { status: 403 }));
    const handler = createExecutionHandler(env(controlFetch), () => ({ execute } as never));
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    }));

    expect(response.status).toBe(403);
    expect(execute).not.toHaveBeenCalled();
    expect(controlFetch).toHaveBeenCalledOnce();
  });

  it("rejects a modified source or manifest before contacting the control plane", async () => {
    const envelope = await makeEnvelope();
    envelope.source = "return 'tampered';" // Deliberately preserve the original signed digest.
    const controlFetch = vi.fn();
    const execute = vi.fn();
    const handler = createExecutionHandler(env(controlFetch), () => ({ execute } as never));
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    }));

    expect(response.status).toBe(400);
    expect(controlFetch).not.toHaveBeenCalled();
    expect(execute).not.toHaveBeenCalled();
  });

  it("rejects unsupported tool names and network-enabled requests", async () => {
    const envelope = await makeEnvelope({ tool_names: ["workspace.read.extra"] });
    const controlFetch = vi.fn();
    const handler = createExecutionHandler(env(controlFetch), () => ({ execute: vi.fn() } as never));
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    }));

    expect(response.status).toBe(400);
    expect(controlFetch).not.toHaveBeenCalled();
  });

  it("rejects oversized streamed requests before control-plane authorization", async () => {
    const envelope = await makeEnvelope();
    envelope.source = "x".repeat(70_000);
    const controlFetch = vi.fn();
    const handler = createExecutionHandler(env(controlFetch), () => ({ execute: vi.fn() } as never));
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    }));

    expect(response.status).toBe(413);
    expect(controlFetch).not.toHaveBeenCalled();
  });

  it("rejects requests without the JSON content type", async () => {
    const controlFetch = vi.fn();
    const handler = createExecutionHandler(env(controlFetch), () => ({ execute: vi.fn() } as never));
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "text/plain" },
      body: "{}",
    }));

    expect(response.status).toBe(415);
    expect(controlFetch).not.toHaveBeenCalled();
  });

  it("rejects any request that attempts to enable outbound networking", async () => {
    const envelope = await makeEnvelope();
    const unsafe = { ...envelope, network_access: true };
    const controlFetch = vi.fn();
    const handler = createExecutionHandler(env(controlFetch), () => ({ execute: vi.fn() } as never));
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(unsafe),
    }));

    expect(response.status).toBe(400);
    expect(controlFetch).not.toHaveBeenCalled();
  });

  it("enforces the manifest call budget before dispatching another host tool", async () => {
    const envelope = await makeEnvelope({ max_tool_calls: 1 });
    const controlFetch = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input)).pathname;
      return Response.json(path.endsWith("/tool-call") ? { authorized: true, result: {} } : { authorized: true });
    });
    const execute = vi.fn(async (_source: string, providers: Array<{ name: string; fns: Record<string, (...args: unknown[]) => Promise<unknown>> }>) => {
      await providers[0].fns.list({});
      await expect(providers[0].fns.list({})).rejects.toThrow("tool-call budget exhausted");
      return { result: { ok: true }, logs: [] };
    });
    const handler = createExecutionHandler(env(controlFetch), () => ({ execute } as never));
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    }));

    expect(response.status).toBe(200);
    expect(controlFetch).toHaveBeenCalledTimes(3);
  });
  it("passes the signed timeout to the sandbox and durably reports sandbox failures", async () => {
    const envelope = await makeEnvelope({ timeout_seconds: 23 });
    const calls: Array<{ path: string; body: Record<string, unknown> }> = [];
    const controlFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input)).pathname;
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      calls.push({ path, body });
      return Response.json({ authorized: true });
    });
    const executorFactory = vi.fn((_loader: Env["LOADER"], timeoutMs: number) => ({
      execute: vi.fn(async () => ({ error: "untrusted internal detail", logs: [] })),
    } as never));
    const handler = createExecutionHandler(env(controlFetch), executorFactory);
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    }));

    expect(executorFactory).toHaveBeenCalledOnce();
    expect(executorFactory.mock.calls[0][1]).toBe(23_000);
    expect(response.status).toBe(422);
    const responseBody = await response.json();
    expect(responseBody).toMatchObject({
      status: "failed",
      error: "sandbox_execution_failed",
    });
    expect(calls.map((call) => call.path)).toEqual([
      "/v1/internal/executions/authorize",
      "/v1/internal/executions/complete",
    ]);
    expect(calls[1].body).toMatchObject({
      execution_id: envelope.execution_id,
      manifest_sha256: envelope.manifest_sha256,
      status: "failed",
      tool_calls: 0,
    });
    expect(JSON.stringify(responseBody)).not.toContain("untrusted internal detail");
  });

  it("fails closed when terminal completion cannot be persisted", async () => {
    const envelope = await makeEnvelope();
    const calls: string[] = [];
    const controlFetch = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input)).pathname;
      calls.push(path);
      if (path.endsWith("/complete")) {
        return Response.json({ authorized: false }, { status: 503 });
      }
      return Response.json({ authorized: true });
    });
    const handler = createExecutionHandler(
      env(controlFetch),
      () => ({ execute: vi.fn(async () => ({ result: { ok: true }, logs: [] })) } as never),
    );
    const response = await handler(new Request("https://adapter.internal/v1/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    }));

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "execution_failed" });
    expect(calls).toEqual([
      "/v1/internal/executions/authorize",
      "/v1/internal/executions/complete",
      "/v1/internal/executions/complete",
    ]);
  });

});
