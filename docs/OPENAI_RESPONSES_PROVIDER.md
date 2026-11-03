# OpenAI Responses API provider

Yuki now includes an optional Responses API adapter alongside the existing
OpenAI-compatible Chat Completions adapter.

## Configuration

Set credentials outside the repository:

    export OPENAI_API_KEY="..."
    export YUKI_OPENAI_RESPONSES_MODEL="gpt-6-astra"
    export YUKI_OPENAI_REASONING_EFFORT="medium"
    export YUKI_OPENAI_MAX_OUTPUT_TOKENS="8192"

Select `provider: openai_responses` in the trusted application configuration.
The model ID is explicit and must be available to the configured OpenAI project.
The adapter does not silently change models if the requested model is unavailable.

Reasoning effort accepts `low`, `medium`, `high`, `xhigh`, or `max` in
this initial integration. Support is model-dependent; confirm the chosen
model's API documentation. The adapter uses the Responses API endpoint.

## Safety boundary

This initial adapter is text-generation only. It deliberately does not pass
model-proposed tools to the API and does not execute function calls, shell
commands, computer actions, or file edits. Yuki's existing worker and
authorization layers remain responsible for any future tool execution.

Credentials are read only from the configured environment variable. The
adapter inherits Yuki's trusted HTTPS host validation, rejects redirects,
bounds prompt and response sizes, and redacts transport errors.

## Astra-inspired follow-up

The OpenAI reasoning guide and deployment checklist motivate future work:
- role-based reasoning effort selected from measured task complexity;
- Responses API conversation continuity and safe mid-task steering;
- background tasks with durable polling and cancellation;
- structured outputs for agent handoffs;
- function calling routed only through Yuki's allow-listed handlers;
- isolated computer-use integration with explicit approvals;
- model evaluations comparing task success, latency, token usage and cost.

Do not infer API availability or capabilities from a model display name.
Evaluate representative Yuki tasks and confirm the model's project access
before enabling a model in production.
