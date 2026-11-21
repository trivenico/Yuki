import { DynamicWorkerExecutor } from "@cloudflare/codemode";
import type { DynamicWorkerExecutor as DynamicWorkerExecutorType } from "@cloudflare/codemode";

type Loader = ConstructorParameters<typeof DynamicWorkerExecutor>[0]["loader"];
type CodeExecutor = Pick<DynamicWorkerExecutorType, "execute">;

interface ControlPlaneBinding {
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
}

export interface Env {
  LOADER: Loader;
  CONTROL_PLANE: ControlPlaneBinding;
}

export interface ExecutionEnvelope {
  execution_id: string;
  actor_id: string;
  session_id: string;
  task_id: string;
  source: string;
  source_sha256: string;
  manifest_sha256: string;
  grant: string;
  tool_names: string[];
  timeout_seconds: number;
  max_tool_calls: number;
  max_result_bytes: number;
  network_access: false;
}

interface ExecutorResult {
  result?: unknown;
  error?: string;
  logs?: string[];
}

const MAX_SOURCE_BYTES = 65_536;
const MAX_TOOLS = 32;
const MAX_GRANT_BYTES = 4096;
const MAX_LOG_BYTES = 16_384;
const MAX_CONTROL_RESPONSE_BYTES = 65_536;
class PayloadTooLargeError extends Error {}
const IDENTIFIER = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const ALLOWED_TOOLS = new Set(["workspace.list", "workspace.read", "workspace.search"]);
const EXECUTION_ID = /^[A-Za-z0-9_.:-]{1,160}$/;

function jsonResponse(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function byteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((part) => part.toString(16).padStart(2, "0")).join("");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validateEnvelope(value: unknown): ExecutionEnvelope {
  if (!isRecord(value)) throw new Error("invalid request");
  const fields = value as unknown as ExecutionEnvelope;
  for (const key of ["execution_id", "actor_id", "session_id", "task_id"] as const) {
    if (typeof fields[key] !== "string" || !EXECUTION_ID.test(fields[key])) throw new Error("invalid request");
  }
  if (typeof fields.source !== "string" || !fields.source.trim() || fields.source.includes("\0")) {
    throw new Error("invalid request");
  }
  if (byteLength(fields.source) > MAX_SOURCE_BYTES) throw new PayloadTooLargeError("source exceeded limit");
  if (typeof fields.grant !== "string" || !fields.grant || byteLength(fields.grant) > MAX_GRANT_BYTES) throw new Error("invalid request");
  if (!Array.isArray(fields.tool_names) || fields.tool_names.length > MAX_TOOLS || fields.tool_names.some((name) => typeof name !== "string")) {
    throw new Error("invalid request");
  }
  if (new Set(fields.tool_names).size !== fields.tool_names.length) throw new Error("invalid request");
  for (const name of fields.tool_names) {
    const parts = name.split(".");
    if (parts.length !== 2 || !IDENTIFIER.test(parts[0]) || !IDENTIFIER.test(parts[1]) || !ALLOWED_TOOLS.has(name)) throw new Error("unsupported tool name");
  }
  if (!Number.isInteger(fields.timeout_seconds) || fields.timeout_seconds < 1 || fields.timeout_seconds > 300) throw new Error("invalid request");
  if (!Number.isInteger(fields.max_tool_calls) || fields.max_tool_calls < 0 || fields.max_tool_calls > 100) throw new Error("invalid request");
  if (!Number.isInteger(fields.max_result_bytes) || fields.max_result_bytes < 1 || fields.max_result_bytes > 1_048_576) throw new Error("invalid request");
  if (fields.network_access !== false) throw new Error("network access is disabled");
  if (!/^[0-9a-f]{64}$/.test(fields.source_sha256) || !/^[0-9a-f]{64}$/.test(fields.manifest_sha256)) throw new Error("invalid request");
  return fields;
}

async function verifyDigests(request: ExecutionEnvelope): Promise<void> {
  const sourceDigest = await sha256(request.source);
  if (sourceDigest !== request.source_sha256) throw new Error("source digest mismatch");
  const manifest = {
    execution_id: request.execution_id,
    actor_id: request.actor_id,
    session_id: request.session_id,
    task_id: request.task_id,
    source_sha256: sourceDigest,
    tool_names: request.tool_names,
    timeout_seconds: request.timeout_seconds,
    max_tool_calls: request.max_tool_calls,
    max_result_bytes: request.max_result_bytes,
    network_access: false,
  };
  if (await sha256(canonicalJson(manifest)) !== request.manifest_sha256) throw new Error("manifest digest mismatch");
}

async function readBoundedBody(
  body: ReadableStream<Uint8Array> | null,
  maxBytes: number,
): Promise<string> {
  if (!body) return "";
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel("body limit exceeded");
        throw new Error("body exceeded limit");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const joined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(joined);
}

async function controlPlaneCall(
  binding: ControlPlaneBinding,
  path: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const response = await binding.fetch(`https://yuki-control-plane.internal${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", "cache-control": "no-store" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  });
  const raw = await readBoundedBody(response.body, MAX_CONTROL_RESPONSE_BYTES);
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { throw new Error("control plane returned invalid JSON"); }
  if (!response.ok || !isRecord(parsed) || parsed.authorized !== true) throw new Error("control plane denied execution");
  return parsed;
}

function buildProviders(
  request: ExecutionEnvelope,
  binding: ControlPlaneBinding,
): {
  providers: Array<{ name: string; fns: Record<string, (...args: unknown[]) => Promise<unknown>> }>;
  callCount: () => number;
} {
  const providers = new Map<string, Record<string, (...args: unknown[]) => Promise<unknown>>>();
  let callIndex = 0;
  for (const toolName of request.tool_names) {
    const [namespace, method] = toolName.split(".");
    const fns = providers.get(namespace) ?? {};
    fns[method] = async (...args: unknown[]): Promise<unknown> => {
      if (callIndex >= request.max_tool_calls) throw new Error("tool-call budget exhausted");
      if (args.length !== 1 || !isRecord(args[0])) throw new Error("tool arguments must be one JSON object");
      const encoded = JSON.stringify(args[0]);
      if (byteLength(encoded) > 32_768) throw new Error("tool arguments exceed limit");
      const index = callIndex++;
      const result = await controlPlaneCall(binding, "/v1/internal/executions/tool-call", {
        grant: request.grant,
        execution_id: request.execution_id,
        actor_id: request.actor_id,
        session_id: request.session_id,
        task_id: request.task_id,
        manifest_sha256: request.manifest_sha256,
        tool_name: toolName,
        call_index: index,
        arguments: args[0],
      });
      if (!("result" in result)) throw new Error("control plane omitted tool result");
      const resultJson = JSON.stringify(result.result);
      if (byteLength(resultJson) > 65_536) throw new Error("tool result exceeds limit");
      return result.result;
    };
    providers.set(namespace, fns);
  }
  return {
    providers: [...providers].map(([name, fns]) => ({ name, fns })),
    callCount: () => callIndex,
  };
}

export function createExecutionHandler(
  env: Env,
  executorFactory: (loader: Loader, timeoutMs: number) => CodeExecutor =
    (loader, timeoutMs) => new DynamicWorkerExecutor({ loader, timeout: timeoutMs, globalOutbound: null }),
): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST" || new URL(request.url).pathname !== "/v1/execute") {
      return jsonResponse(404, { error: "not_found" });
    }
    if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
      return jsonResponse(415, { error: "unsupported_media_type" });
    }
    const declaredLength = Number(request.headers.get("content-length") ?? "0");
    if (declaredLength > MAX_SOURCE_BYTES + 16_384) return jsonResponse(413, { error: "request_too_large" });
    let raw: string;
    try {
      raw = await readBoundedBody(request.body, MAX_SOURCE_BYTES + 16_384);
    } catch {
      return jsonResponse(413, { error: "request_too_large" });
    }

    let envelope: ExecutionEnvelope;
    try {
      envelope = validateEnvelope(JSON.parse(raw));
      await verifyDigests(envelope);
    } catch (error) {
      if (error instanceof PayloadTooLargeError) return jsonResponse(413, { error: "source_too_large" });
      return jsonResponse(400, { error: "invalid_execution_envelope" });
    }

    try {
      // This call is authoritative: Yuki must atomically validate and consume
      // the one-shot grant/execution ID before any generated code is started.
      await controlPlaneCall(env.CONTROL_PLANE, "/v1/internal/executions/authorize", {
        grant: envelope.grant,
        execution_id: envelope.execution_id,
        actor_id: envelope.actor_id,
        session_id: envelope.session_id,
        task_id: envelope.task_id,
        manifest_sha256: envelope.manifest_sha256,
        tool_names: envelope.tool_names,
        timeout_seconds: envelope.timeout_seconds,
        max_tool_calls: envelope.max_tool_calls,
        max_result_bytes: envelope.max_result_bytes,
      });
    } catch {
      return jsonResponse(403, { error: "execution_not_authorized" });
    }

    const started = Date.now();
    let toolCallCount = () => 0;
    try {
      const executor = executorFactory(env.LOADER, envelope.timeout_seconds * 1000);
      const toolBridge = buildProviders(envelope, env.CONTROL_PLANE);
      toolCallCount = toolBridge.callCount;
      const result = await executor.execute(envelope.source, toolBridge.providers) as ExecutorResult;
      const elapsed = (Date.now() - started) / 1000;
      const status = result.error ? "failed" : "succeeded";
      const serializedResult = JSON.stringify(result.result ?? null);
      if (typeof serializedResult !== "string" || byteLength(serializedResult) > envelope.max_result_bytes) {
        throw new Error("execution result exceeded limit");
      }
      const logs = Array.isArray(result.logs) ? result.logs.filter((line): line is string => typeof line === "string").join("\n") : "";
      const boundedLogs = new TextEncoder().encode(logs).slice(0, MAX_LOG_BYTES);
      const logText = new TextDecoder().decode(boundedLogs);
      await controlPlaneCall(env.CONTROL_PLANE, "/v1/internal/executions/complete", {
        grant: envelope.grant,
        execution_id: envelope.execution_id,
        manifest_sha256: envelope.manifest_sha256,
        status,
        duration_seconds: elapsed,
        tool_calls: toolBridge.callCount(),
      });
      return jsonResponse(status === "succeeded" ? 200 : 422, {
        status,
        result: result.result ?? null,
        error: result.error ? "sandbox_execution_failed" : undefined,
        logs: logText,
        duration_seconds: elapsed,
      });
    } catch {
      try {
        await controlPlaneCall(env.CONTROL_PLANE, "/v1/internal/executions/complete", {
          grant: envelope.grant,
          execution_id: envelope.execution_id,
          manifest_sha256: envelope.manifest_sha256,
          status: "failed",
        });
      } catch {
        // The native audit/control plane records an incomplete execution for reconciliation.
      }
      return jsonResponse(502, { error: "execution_failed" });
    }
  };
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return createExecutionHandler(env)(request);
  },
};
