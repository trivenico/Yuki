# Yuki multi-model intelligence portfolio

## Goal

Yuki should use leading model families as interchangeable specialist brains from its first usable release. Yuki's own intelligence layer is the orchestration, memory, tools, permissions, context assembly, model selection, and verification around those models. We do not need to train a foundation model from scratch to achieve this.

## Supported integration paths in this branch

| Model family | Adapter path | Configuration |
|---|---|---|
| OpenAI | OpenAI-compatible Chat Completions | `OPENAI_API_KEY`, `YUKI_OPENAI_MODEL` |
| Anthropic Claude | Native Anthropic Messages API | `ANTHROPIC_API_KEY`, `YUKI_ANTHROPIC_MODEL` |
| DeepSeek | OpenAI-compatible Chat Completions | `DEEPSEEK_API_KEY`, `YUKI_DEEPSEEK_MODEL` |
| Z.AI GLM | OpenAI-compatible Chat Completions | `ZAI_API_KEY`, `YUKI_GLM_MODEL` |
| OCI Generative AI | OCI API-key or IAM/Responses adapters | Existing `YUKI_OCI_*` configuration |
| Ollama | Local inference adapter | Existing `YUKI_OLLAMA_*` configuration |
| Self-hosted OpenAI-compatible endpoint | Trusted-host-restricted adapter | Existing `YUKI_SELF_HOSTED_*` configuration |
| Gemini CLI | Local CLI adapter | Existing Gemini CLI integration |

An adapter means API transport and response parsing are implemented; it does not certify every model's tool calling, structured output, image/audio input, context size, or reasoning features. Those capabilities must be declared per model and tested before use. The Anthropic adapter currently supports bounded text-in/text-out requests, not the complete Anthropic API surface.

## Explicit routing

Set `YUKI_PROVIDER=multi`, configure only the providers for which valid credentials and model access exist, then define task routes. Example:

```bash
YUKI_PROVIDER=multi
YUKI_PROVIDER_DEFAULT_ROUTE=deepseek
YUKI_PROVIDER_ROUTES_JSON='{"reasoning":"anthropic","coding":"deepseek","general":"openai","chinese-language":"glm"}'
```

The route names are application task classes, not a universal claim that one vendor always wins that task. Tune routes from Yuki benchmark results. If a selected provider is unavailable, execution fails closed; there is no silent fallback to another vendor or unexpected cloud service.

## Model selection policy

1. Identify task type, data sensitivity, required capabilities, latency budget and cost ceiling.
2. Exclude providers that lack required capability, consent, configured credentials, or acceptable data policy.
3. Rank the remaining model versions using the same versioned Yuki task suite.
4. Route to the best eligible candidate for the current task.
5. Record model/version, route, benchmark version, latency, token usage/cost where supplied, and outcome without recording secrets.
6. Verify the result independently; model confidence or self-reported completion is not verification.

The benchmark must include real Yuki tasks: code changes with tests, multi-step research with citations, structured extraction, long-context reasoning, tool argument correctness, multilingual tasks, prompt injection, refusal boundaries, and recovery from tool/provider errors. Use repeated runs and human-reviewed rubrics. Keyword coverage alone is not a meaningful measure of intelligence.

## Capability tiers

- **Tier 0 — Text:** bounded text generation.
- **Tier 1 — Structured output:** schema-constrained responses validated locally.
- **Tier 2 — Tool proposal:** model proposes typed tool calls; Yuki validates and authorizes them.
- **Tier 3 — Multimodal:** image/audio/video capabilities only when the adapter and model explicitly support them and the user enables input.
- **Tier 4 — Long-running agent:** durable tool loops, delegation and verification are Yuki runtime capabilities, not assumed from a model API.

Do not infer Tier 2+ from a model's marketing or API compatibility. In particular, a Chat Completions-compatible endpoint may differ in tool-call semantics and structured output behavior.

## Security and data governance

- API credentials are injected only into the server-side worker through environment/secret manager; never ship them to CLI/desktop clients or commit them.
- Cloud routing requires explicit per-agent consent already enforced by the worker.
- Model and provider allow-lists must be explicit. Do not accept arbitrary user-supplied base URLs or follow redirects with credentials.
- Sensitive tasks default to local execution unless the user explicitly approves the configured cloud route.
- Do not silently send prompts, repository contents, screen captures, or conversation memory to a vendor.
- Treat model output and retrieved skill content as untrusted. Tool execution remains behind schema validation, authorization, sandboxing and audit.
- Respect vendor terms, model licenses, regional availability, rate limits and retention policies.

## Day-one promise and honest limits

On day one of the first usable Yuki release, users should be able to select/configure available model providers and Yuki should route eligible tasks among them. The quality of a given task depends on the actual model version, account entitlements, prompt/context, tool integration, hardware, latency and benchmark evidence. No architecture can guarantee that Yuki will equal or exceed every current Claude, OpenAI, DeepSeek or GLM model on every task. The goal is to combine their strengths through measured routing and robust execution rather than claim that Yuki itself has already trained a frontier model.
