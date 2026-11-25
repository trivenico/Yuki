# OCI private pilot provisioning

This directory is reserved for reviewed infrastructure-as-code. **No resources are provisioned by this document.** Region and tenancy remain deliberately unset until the owner chooses them.

## Required inputs before an apply

- OCI tenancy OCID and target compartment OCID, supplied through the operator's authenticated OCI CLI/profile or workload identity.
- OCI region selected by the owner after checking service availability, data-residency needs, latency, and price.
- Approved Linux image OCID for that region.
- Compute shape selected from measured Yuki runtime needs; hosted inference is the initial default, local inference is a separately evaluated fallback.
- Boot/data volume sizes, backup retention, maintenance window, and monthly spend alert threshold.
- Admin ingress path: OCI Bastion or private VPN. No unrestricted public SSH.
- Network CIDRs and required egress destinations, reviewed before apply.

## Provisioning sequence

1. Verify tenancy/compartment access and region availability using the authenticated OCI CLI.
2. Create a dedicated VCN, private subnet, route/security rules, and an administrative Bastion/private VPN path.
3. Create least-privilege IAM policies and a dynamic group for the instance; avoid user API keys.
4. Create one pilot compute instance with encrypted persistent data storage and resource limits.
5. Configure Vault secret access, patching, time synchronization, log shipping, monitoring, budget alerts, and backup policy.
6. Deploy a pinned Yuki commit to private staging only after application/security bootstrap gates are satisfied.
7. Rehearse restore, reboot recovery, and rollback before inviting pilot use.

## Hard stops

- Do not apply infrastructure until region, tenancy, compartment, shape, CIDRs, budget, and operator access are explicitly confirmed.
- Do not put OCIDs that identify private tenancy resources, private keys, tokens, or passwords in Git.
- Do not expose the stock developer web launcher to the internet. Yuki's strict production web composition requires deployment-owned identity, policy, PostgreSQL security runtime, durable audit writer, and independent audit checkpoint anchor.
- Do not grant Kaggle jobs production OCI credentials or direct deployment permission.
- Do not run Terraform apply from pull-request workflows. Plan output must be reviewed; apply must be a separately approved operator action.
- Keep all ingress private for the first pilot. Public ingress is a separate security/release decision.

## Hybrid inference decision

Start with hosted inference behind Yuki's existing provider abstraction. Add local inference only as a separately configured provider with bounded resources, health checks, explicit routing policy, and the same regression/safety evaluation suite. Fail closed rather than silently switching providers when the fallback is not healthy or approved.
