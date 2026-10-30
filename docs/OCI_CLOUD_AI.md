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

OCI Generative AI API-key authentication is currently documented for six regions: Germany Central (Frankfurt), India South (Hyderabad), Japan Central (Osaka), US East (Ashburn), US Midwest (Chicago), and US West (Phoenix). Model availability must also overlap the selected region. UK South (London) has H100 hardware listed for dedicated AI clusters, but it is not in the documented six-region API-key list. OCI IAM-based authentication is the intended production option and is documented separately. Yuki's current adapter implements API-key authentication only; IAM request signing is not implemented yet. See Oracle's [API-key region requirements](https://docs.oracle.com/en-us/iaas/Content/generative-ai/api-keys.htm), [project setup](https://docs.oracle.com/en-us/iaas/Content/generative-ai/use-project.htm), and [IAM authentication](https://docs.oracle.com/en-us/iaas/Content/generative-ai/oci-genai-auth.htm).

Select `oci` as the provider in the server configuration. The adapter builds:

`https://inference.generativeai.<region>.oci.oraclecloud.com/openai/v1`

and sends the `openai-project` header with the project OCID. The endpoint hostname is allow-listed for that configured region. Credentials are read from environment variables, never committed to the repository or desktop config.

This first adapter uses the Chat Completions-compatible interface. Oracle now recommends the Responses API for agentic workloads; it supports stateful conversations and OCI-supported tools including file search, code interpreter, function calling, and MCP calling. A separate Responses API adapter should be added before using OCI-managed agent tools; do not assume Chat Completions provides those capabilities. See Oracle's [OpenAI-compatible endpoints](https://docs.oracle.com/en-us/iaas/Content/generative-ai/openai-compatible-api.htm) and [Responses API](https://docs.oracle.com/en-us/iaas/Content/generative-ai/responses-api.htm).

## OCI IAM authentication

For production services running inside OCI, select `provider: oci_iam` and use the Oracle-maintained `oci-genai-auth` signer. Install the pinned optional dependency with:

```bash
pip install -r requirements-oci.txt
```

Supported authentication modes:

- `resource_principal`: recommended for supported OCI-hosted application resources.
- `instance_principal`: for Yuki running directly on OCI Compute instances with the appropriate dynamic group and IAM policy.
- `session`: for a developer using an OCI CLI session profile.
- `user_principal`: for a configured OCI user API-key profile.

Configuration example:

```yaml
provider: oci_iam
oci_region: eu-frankfurt-1
oci_model: <model-id-enabled-for-the-project>
oci_project_ocid: <generative-ai-project-ocid>
oci_auth_mode: resource_principal
```

For local development, set `oci_auth_mode: session` and optionally `YUKI_OCI_CONFIG_FILE` / `YUKI_OCI_PROFILE`. Do not copy local OCI config/private keys into desktop application settings or the repository. On OCI, assign only the least-privilege IAM policy required to invoke the chosen Generative AI project/model. Instance/resource principal support depends on the OCI service environment and IAM policy.

In `provider: multi`, set `YUKI_OCI_AUTH_MODE=resource_principal` (or another supported mode) to select IAM for the managed OCI route; use `api_key` to retain the existing API-key adapter. Set `YUKI_OCI_API_MODE=responses` to select the Responses API when using IAM, or leave it as `chat_completions` for the standard chat endpoint. The standalone provider name `oci_responses` selects Responses API directly. The IAM provider uses Oracle's signer with token refresh, keeps redirects disabled, and sends the project OCID header. Yuki now also provides `provider: oci_responses`, which calls the OCI Responses endpoint using the same IAM signer and project header. It extracts `output_text` or standard output content. This is the preferred integration surface to build stateful agent workflows on; tool execution, conversation-state persistence, and MCP orchestration still need to be implemented in Yuki's agent runtime rather than assumed to exist just because the model endpoint supports them.

The dependency and UPL attribution are recorded in `requirements-oci.txt`, `THIRD_PARTY_NOTICES.md`, and `licenses/UPL-1.0-Oracle-oci-genai-auth.txt`.

## Explicit multi-model routing in Yuki

Yuki now includes a `multi` provider mode that can configure both OCI-managed inference and a separately hosted OpenAI-compatible model server at the same time. Task names are explicitly mapped to providers; the router does not silently fall back when a selected provider is unavailable.

Example non-secret configuration:

```yaml
provider: multi
provider_default_route: oci_managed
provider_routes:
  reasoning: oci_managed
  general: oci_managed
  coding: self_hosted
  code_review: self_hosted
```

Managed-model environment (secrets must come from OCI Vault or a server secret manager):

- `YUKI_OCI_REGION=eu-frankfurt-1`
- `YUKI_OCI_MODEL=<model-id-available-in-region>`
- `YUKI_OCI_PROJECT_OCID=<project-ocid>`
- `OCI_GENAI_API_KEY=<secret>`

Self-hosted inference environment:

- `YUKI_SELF_HOSTED_MODEL=<exact-model-id>`
- `YUKI_SELF_HOSTED_BASE_URL=https://<private-inference-host>/v1`
- `YUKI_CLOUD_ALLOWED_HOSTS=<exact-inference-hostname>`
- `YUKI_SELF_HOSTED_API_KEY=<secret>`

The self-hosted endpoint must use HTTPS and its exact hostname must be allow-listed. Do not expose the inference server directly to the public internet; place it in a private subnet and allow ingress only from the Yuki gateway. The current adapter uses a bearer key for the self-hosted endpoint. A private DNS hostname with a valid TLS certificate is supported; raw private IP URLs are deliberately rejected. Configure routes only for task categories whose data policy permits that provider.

The `multi` router validates that every configured task route references a provider that is actually configured. It never retries a failed request against another provider, because silent cross-provider fallback could leak private prompts or change model behavior. Add an explicit fallback policy only after privacy classification, consent, and audit are wired into the gateway.

## Hosted models versus Yuki-owned models

There are two different operating modes:

1. **OCI-hosted managed models** — select models Oracle makes available in the target region. Oracle manages model serving. Availability, model list, features, and prices vary by region/project.
2. **Yuki-hosted open-weight models** — deploy model weights we are licensed to use on OCI GPU compute or a dedicated AI cluster. Yuki owns the serving stack, model routing, versioning, evaluation, and deployment lifecycle. We must follow each model's licence and usage terms.

OCI Generative AI can host supported pretrained, custom, and imported models on dedicated AI clusters. Oracle also documents importing models from Object Storage or Hugging Face and hosting them on a dedicated cluster. A self-managed OCI Compute GPU instance can instead run a serving stack such as vLLM or TGI, subject to the model's supported runtime and hardware needs.

A sensible first self-hosted candidate evaluation set is a compact instruct model for low-cost routing, a strong coding model, and a larger reasoning/general model. Candidate families include Qwen, DeepSeek, and Mistral; pin exact model revisions and verify licence, hardware fit, context length, quantization quality, and benchmark performance before deployment. Do not assume all models in a family have identical licences or commercial terms.

OpenAI and Anthropic do not publish the proprietary weights behind their flagship GPT and Claude models for us to self-host. OCI's currently documented model catalogue includes specific offerings such as Meta Llama, OpenAI gpt-oss, and xAI Grok; it does not mean that proprietary GPT or Claude weights are available. Check exact model and region availability before selecting the deployment.

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

## Starter-budget deployment choice

Do not begin with a permanently running multi-GPU dedicated AI cluster. For the first usable cloud version:

1. Use OCI's on-demand managed inference for the always-available general/reasoning model. This avoids reserving a dedicated GPU cluster while the workload is small.
2. Add one self-hosted open-weight model as a separately metered OpenAI-compatible inference service on a single GPU Compute instance, initially started only for development, coding batches, or scheduled workloads. Stop it when idle if the chosen compute shape and deployment support safe shutdown; persistent storage must keep model artifacts and configuration.
3. Route tasks explicitly between managed OCI inference and the self-hosted endpoint. Do not silently send private prompts to external APIs. Keep the existing local-first and cloud-consent policy.
4. Add a monthly budget alert and compartment-level cost tracking before creating any GPU resource. Add a hard operator-controlled kill switch for self-hosted GPU compute.
5. Measure tokens/second, time-to-first-token, concurrent sessions, VRAM use, and actual monthly spend before increasing replicas or moving to dedicated AI clusters.

OCI documents a 744-unit-hour minimum commitment for dedicated clusters hosting OCI pretrained/custom models; imported-model clusters have a different billing treatment and still incur AI-unit-hour charges. Dedicated cluster cost is therefore not the starter default. Check current pricing and service limits in the target tenancy before provisioning. See [dedicated cluster endpoint and billing details](https://docs.oracle.com/en-us/iaas/Content/generative-ai/endpoint.htm), [hardware by region](https://docs.oracle.com/en-us/iaas/Content/generative-ai/hardware-unit-shapes-by-region.htm), and the [OCI cost estimator](https://www.oracle.com/cloud/costestimator.html).

## Region selection for a flexible deployment

Do not hard-code a region until the OCI tenancy has been checked. Select the first region that satisfies all of these gates:

- The required managed model is available there.
- The required authentication method is supported there.
- A suitable GPU Compute shape or dedicated-cluster shape is available and the tenancy has service limits/quota.
- The estimated monthly cost fits the approved starter budget.
- Network latency and data-residency requirements are acceptable.

For API-key based initial testing, Frankfurt and Chicago are useful candidates because they are in the documented API-key region set and have H100 dedicated-cluster shapes listed. London has H100 dedicated-cluster hardware listed, but the current API-key region list does not include London; London would therefore require IAM auth for the managed endpoint, which Yuki does not implement yet. This is a capability comparison, not a final region selection. Oracle's region/model and hardware tables change; re-check them before deployment.

## Deployment status

The repository contains the OCI provider adapter and configuration tests. No OCI tenancy has been provisioned or connected from this development session. The gateway, cloud-hosted model servers, Vault/IAM policies, network, billing alerts, device authentication, and desktop/CLI connection are still to be implemented and deployed. Do not put OCI secrets in source control or send them in chat.

## Per-device identity and revocation foundation

The gateway's device identity primitive is implemented in `cloud/device_registry.py` as `DeviceCredentialStore`.

- Each installation receives a separate high-entropy opaque token with a `yuki_dev_` prefix.
- Only the SHA-256 token digest is persisted in SQLite; the raw token is returned once at issuance.
- Each token is associated with an authenticated Yuki user, a device name, and a least-privilege scope set.
- The registry supports per-user device listing and owner-scoped revocation. Revoked credentials fail authentication immediately.
- Default scopes are limited to profile read, chat write, and task read/write. There are no wildcard/admin scopes.
- The module deliberately does not expose public self-registration or an HTTP endpoint. A future gateway route must first authenticate the owning user/admin, verify CSRF/origin protections for browser sessions, rate-limit issuance, return the raw token only once over TLS, and audit issue/revoke operations.
- Desktop and CLI clients must store their device token in the operating system's credential store (Keychain, Credential Manager, or Secret Service), never in project YAML or source control.

This is the credential storage/authentication foundation, not yet a deployed gateway. HTTPS termination, authenticated enrollment endpoints, device management UI/CLI commands, refresh/rotation workflow, per-route scope enforcement, and production rate limiting remain outstanding. Until those layers are complete, do not expose the registry directly to an untrusted network.
