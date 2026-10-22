# Provider catalog and task-aware routing (Phase 41)

## What this phase adds

- A static, machine-readable catalog distinguishes implemented adapters from planned providers.
- `TaskRouter` accepts already-constructed adapters and routes only explicitly configured task names.
- The default route remains local; unavailable or failing routes never trigger an implicit fallback.
- Tests cover explicit routing, provider errors, unavailable routes, invalid names, and catalog accuracy.

## Current provider truth

The catalog is not a live model inventory. Listing a provider does not prove that a model, account login, free quota, or endpoint is available. A UI should offer only entries with `implemented: true`. Model IDs, context limits, pricing, capabilities, and quota data must come from verified provider metadata, with freshness tracked separately.

The existing factory wires mock, Ollama, and hybrid modes. Hybrid can use the generic OpenAI-compatible adapter when explicitly configured. OpenAI, Anthropic/Claude, Gemini, DeepSeek, Kimi/Moonshot, MiniMax, GLM/Z.ai, OpenRouter, and TokenRouter are not dedicated, catalog-enabled runtime choices in this phase.

## Integration example

```python
from providers.task_router import TaskRouter
router = TaskRouter(
    providers={"ollama": local_provider, "cloud": approved_cloud_provider},
    default_provider="ollama",
    task_routes={"deep_research": "cloud"},
)
answer = router.generate_for("deep_research", "Summarize the approved research task")
```

Only pass providers created from validated configuration. Never construct endpoints from model output. Keep keys in environment variables or a secret manager, not YAML, logs, tests, or this catalog. Remote routing may disclose prompt/context to the provider; the product layer must obtain explicit consent before sending private data.

## Deliberate limitations

This phase does not fetch live model lists, scrape provider websites, add streaming, native tool calling, structured-output guarantees, token accounting, retry/backoff, health-based failover, or cancellation. It does not claim consumer-subscription account login. Router integration into the legacy terminal loop and workload evaluation remain follow-up work. Do not retry authentication errors, invalid requests, budget failures, policy refusals, or private prompts against another provider automatically.

## Verification

Run `python -m unittest tests.test_task_router -v` and `python -m unittest discover -s tests -v`. These tests have not been executed in a local Python environment from the GitHub editing session; CI must confirm before merge.
