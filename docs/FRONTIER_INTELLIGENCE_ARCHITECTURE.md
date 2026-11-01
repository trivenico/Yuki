# Yuki Unified Frontier Intelligence

## Goals

Yuki is the orchestration and execution-control platform. Model providers are
replaceable inference backends; they do not own durable task state, user
permissions, tool authorization, workspace writes, or audit history.

## Current provider adapters

| Provider | Adapter | Configuration |
|---|---|---|
| OpenAI Chat Completions | `OpenAICompatibleProvider` | `YUKI_OPENAI_MODEL` |
| OpenAI Responses API | `OpenAIResponsesProvider` | `YUKI_OPENAI_RESPONSES_MODEL` |
| Anthropic Messages API | `AnthropicMessagesProvider` | `YUKI_ANTHROPIC_MODEL` |
| Ollama | `OllamaProvider` | `YUKI_OLLAMA_MODEL` |
| Gemini CLI | `GeminiCLIProvider` | official CLI login |
| Hybrid | `HybridProvider` | explicit per-task cloud allow-list |

The OpenAI Responses and Anthropic adapters are text-only in this iteration.
They do not send model tool definitions or execute model-generated actions.
All API keys are read from environment variables. Provider catalog entries
report sanitized readiness without exposing credential values. Cloud routing
must be explicitly opted into.

## OpenAI configuration

    export OPENAI_API_KEY="..."
    export YUKI_OPENAI_RESPONSES_MODEL="gpt-6-astra"
    export YUKI_OPENAI_REASONING_EFFORT="high"
    export YUKI_OPENAI_MAX_OUTPUT_TOKENS="8192"

Select `provider: openai_responses`. Confirm the model ID and API availability
for the actual OpenAI project before use.

## Anthropic configuration

    export ANTHROPIC_API_KEY="..."
    export YUKI_ANTHROPIC_MODEL="claude-fable-5"
    export YUKI_ANTHROPIC_EFFORT="high"
    export YUKI_ANTHROPIC_MAX_OUTPUT_TOKENS="8192"

Select `provider: anthropic`. The model ID is deliberately configurable.
Fable 5.1 is generally available according to Anthropic's model announcement.
Mythos models are restricted to approved access programs. Yuki does not
assume access: an operator must set the exact API model ID supplied to their
approved account, and Anthropic remains the authority on entitlement.

Do not use a guessed Mythos model ID or treat a local configuration as proof
of entitlement. Provider/API errors must be surfaced without fallback to a
different model.

## Glasswing-inspired defensive engineering

Project Glasswing is an Anthropic defensive-security initiative, not an
open-source model or downloadable scanner. Yuki adopts its defensive lessons:

1. Run deterministic, read-only static analysis and dependency/secret checks.
2. Produce redacted, machine-readable findings with stable rule IDs.
3. Preserve evidence, source revision and scanner version.
4. Require authorized scope and isolated environments for dynamic testing.
5. Separate vulnerability discovery from exploit execution.
6. Require human approval for patches, privileged actions and external tests.
7. Verify fixes with independent tests and a second review.
8. Record audit events and retain a rollback path.

Mythos access is not required to implement these controls. A future model-
assisted reviewer may explain findings and propose fixes, but its output is
untrusted and cannot authorize execution. Do not run untrusted repository
code on the host.

## Fable-inspired long-horizon engineering

Yuki already has a durable task runtime, workflow store, specialist coordinator
and approved workspace-change/test services. The next integration should use
those services as the system of record:

- persist task plan, dependencies, checkpoints and model/provider identity;
- save concise progress summaries before context rollover;
- resume only from validated checkpoints and current authorization;
- use bounded retries with idempotency keys;
- allow user steering by appending a new instruction/event, not rewriting
  completed audit history;
- keep a human-readable progress log;
- require independent tests and diff review before marking work complete.

Do not claim a task succeeded because a model says it succeeded.

## Adaptive model routing (next stage)

Build deterministic task profiles before any learned router:

- routine explanation: economical model and low effort;
- architecture/code review: stronger reasoning and high effort;
- long-running implementation: durable workflow + high-capability provider;
- defensive security: authorized scope, static scanner, independent reviewer;
- private workspace: local provider unless cloud use is explicitly approved.

Route using measured task success, latency, cost, context limits, privacy class,
capability support and current provider readiness. Never infer capabilities
from a model name. Keep a manual override and explain the routing decision.

## Safety and limitations

- Model output is data, never authority.
- No arbitrary shell, browser, filesystem or computer-use tools are enabled by
  these provider adapters.
- Existing Yuki authorization, approvals, approved workspace changes and
  allow-listed test runner remain mandatory.
- Model access, model IDs, effort values, context windows and output ceilings
  are provider/account/model dependent and must be verified at runtime.
- A successful mocked test proves adapter behavior, not live API entitlement,
  model quality, production readiness or security certification.
