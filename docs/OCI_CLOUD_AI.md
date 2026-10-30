# Yuki Cloud AI on Oracle Cloud Infrastructure

## Architecture

Yuki Cloud AI is the planned hosted inference and agent-control plane for the personal Yuki installation:

```text
Yuki CLI / Yuki Desktop
        |
        | HTTPS + per-device Yuki identity
        v
Yuki Cloud Gateway (OCI API Gateway / Load Balancer)
        |
        +--> Authentication, device revocation, quotas and audit
        |
        +--> Yuki Model Router
        |       +--> OCI Generative AI hosted models
        |       +--> Yuki-hosted open-weight models (vLLM/TGI)
        |       +--> External providers (explicitly enabled)
        |
        +--> Durable Agent Runtime / Task Queue
                +--> Yuki specialist agents
                +--> Memory and retrieval
                +--> Schedulers and recovery
        |
        v
Private OCI network, model endpoints, storage and observability
```

The desktop and CLI should authenticate to Yuki's gateway, not hold OCI administrator credentials or model-provider secrets. The gateway keeps OCI API keys in OCI Vault or uses OCI IAM/resource principals when running in OCI. Device credentials should be individually revocable.

## Current provider adapter

Yuki now has an OCI Generative AI provider option through the existing OpenAI-compatible adapter.

Configure these environment variables in the Yuki server process:

- `YUKI_OCI_REGION`: OCI region identifier, for example `us-chicago-1`.
- `YUKI_OCI_MODEL`: exact model identifier enabled for the selected region/project.
- `YUKI_OCI_PROJECT_OCID`: Generative AI project OCID.
- `OCI_GENAI_API_KEY`: OCI Generative AI API key secret.

Select `oci` as the provider in the server configuration. The adapter builds:

`https://inference.generativeai.<region>.oci.oraclecloud.com/openai/v1`

and sends the `openai-project` header with the project OCID. The endpoint hostname is allow-listed for that configured region. Credentials are read from environment variables, never committed to the repository or desktop config.

This first adapter uses the Chat Completions-compatible interface. OCI's Responses API has additional stateful and agent-oriented features; it should be a separate adapter after the gateway/runtime contracts are in place.

## Hosted models versus Yuki-owned models

There are two different operating modes:

1. **OCI-hosted managed models** — select models Oracle makes available in the target region. Oracle manages model serving. Availability, model list, features, and prices vary by region/project.
2. **Yuki-hosted open-weight models** — deploy model weights we are licensed to use on OCI GPU compute or a dedicated AI cluster. Yuki owns the serving stack, model routing, versioning, evaluation, and deployment lifecycle. We must follow each model's licence and usage terms.

A sensible first self-hosted candidate evaluation set is a compact instruct model for low-cost routing, a strong coding model, and a larger reasoning/general model. Candidate families include Qwen, DeepSeek, and Mistral; pin exact model revisions and verify licence, hardware fit, context length, quantization quality, and benchmark performance before deployment. Do not assume all models in a family have identical licences or commercial terms.

## 24/7 service design

- Run the gateway, model router, scheduler, and durable queue as separate supervised services.
- Use OCI IAM/resource principals for OCI-hosted workloads where supported; use OCI Generative AI API keys only for development or where required.
- Store secrets in OCI Vault, rotate them, and never distribute them to CLI/desktop clients.
- Use private subnets for model-serving instances; expose only the authenticated gateway.
- Add health checks, process restart policies, queue leases, retry/backoff, dead-letter handling, database backups, metrics, alerts, and deployment rollback.
- Keep a local mode available when the network or cloud service is unavailable.
- Make user-data residency, retention, backups, and external-provider routing explicit.

## Important limits and cost controls

A personal deployment can avoid Yuki-imposed agent-count or task-count quotas. It cannot create infinite compute. GPU capacity, concurrency, model context, storage, bandwidth, provider quotas, regional capacity, and budget are real constraints.

A GPU instance left running 24/7 can incur continuous charges even while idle. Before provisioning, estimate monthly compute, boot/block storage, object storage, networking/egress, load balancing, logs, backups, and any managed model token charges. Start with a small test deployment and scale after measuring real throughput and latency.

## Deployment status

The repository contains the OCI provider adapter and configuration tests. No OCI tenancy has been provisioned or connected from this development session. The gateway, cloud-hosted model servers, Vault/IAM policies, network, billing alerts, device authentication, and desktop/CLI connection are still to be implemented and deployed. Do not put OCI secrets in source control or send them in chat.
