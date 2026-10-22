# Phase 63 — Model evaluation and recommendation evidence

Yuki can run an explicitly requested, small benchmark against a named model on the Hugging Face Inference Router. This phase evaluates candidate models; it does not discover, download, install, activate or replace them.

## Prerequisites

- Python version supported by this repository.
- A Hugging Face token with access to the chosen Inference Provider/model.
- A model ID supported by the Hugging Face Inference Router's OpenAI-compatible Chat Completions endpoint.
- Review the provider/model terms, data handling and any usage charges before sending prompts.

Set `HF_TOKEN` in your shell or secret manager. Never commit it or pass it as a CLI argument.

## Run the starter benchmark

```bash
python yuki_evaluate_model.py \
  --model organization/model-id \
  --benchmark benchmarks/model_evaluation_starter.json \
  --limit 5 \
  --output evaluation-report.json
```

Replace `organization/model-id` with the exact model identifier you intend to test. The report path must not already exist. Each run makes real inference requests and may consume provider quota or incur charges; tests use mocked HTTP and do not make network requests.

## Benchmark format

A benchmark is a local UTF-8 JSON object with a `cases` array. Each case has a unique ID, prompt, and optional `expected_terms` list:

```json
{
  "name": "example",
  "version": 1,
  "cases": [
    {
      "id": "explain-timeouts",
      "prompt": "Explain why network requests need timeouts.",
      "expected_terms": ["deadline", "failure"]
    }
  ]
}
```

Do not place credentials or confidential customer data in benchmark prompts. Only the first 1–20 cases selected by the CLI are run. Prompt length, expected-term count, timeout, response size and output size are bounded.

## How to interpret reports

- `mean_expected_term_coverage`: fraction of expected strings present, case-insensitively, in each successful answer, averaged across scored cases.
- `mean_latency_ms`: mean wall-clock duration of successful requests; it includes network and provider latency.
- Per-case success/failure, matched terms and missing terms.
- Model ID, source, schema version and an explicit `manual_review_required` activation policy.

Expected-term coverage is deliberately transparent but limited. It can reward shallow keyword stuffing and does not measure correctness, reasoning, security, helpfulness, bias, instruction following or resistance to prompt injection. Empty expected-term lists are not scored. Do not compare models using different prompts, settings, or benchmark versions. Use task-specific expected outputs, human review and adversarial cases before making a decision.

## Security and data boundaries

- Requests go only to the fixed HTTPS host `router.huggingface.co`; model IDs are validated and are placed in the JSON body, not the URL.
- API tokens are read from `HF_TOKEN` and sent as an Authorization header. Reports do not contain prompts, generated answers, or token values.
- Provider errors are redacted in reports; response sizes and request timeouts are bounded.
- No model weights or remote code are downloaded or executed.
- The tool does not auto-activate models. A report is evidence for a human decision, not a certification.
- Do not send sensitive code, personal data or private project content unless your provider and organizational policies explicitly allow it.

## Next evaluation improvements

For production-grade selection, expand this starter harness with a versioned Yuki task dataset, deterministic structured-output validators, code/test execution in a sandbox, rubric-based human review, security/prompt-injection suites, repeat runs and confidence intervals, model/license/runtime compatibility checks, and per-provider cost telemetry. Recommendations should be generated from these measured results and never silently change the active model.
