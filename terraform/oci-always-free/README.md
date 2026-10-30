# Yuki on OCI Always Free

This module provisions a starter host, not the complete production gateway.

## Free-tier sizing

- One `VM.Standard.A1.Flex` instance: 2 OCPUs and 12 GB RAM.
- 50 GB boot volume (counts against the 200 GB combined Always Free block-volume allowance).
- One VCN, internet gateway, route table, public subnet and security list.
- Public ingress is HTTPS/443 only. SSH/22 is disabled unless an explicit trusted CIDR is supplied.
- The instance is ARM64. Container images must publish `linux/arm64` or multi-architecture manifests.

OCI Always Free compute and boot volumes must be created in the tenancy home region. A1 capacity can be temporarily unavailable; try another availability domain in that home region. Oracle may reclaim Always Free compute considered idle under its published utilization policy. Always Free does not provide a contractual uptime SLA.

## Prerequisites

1. An OCI Always Free tenancy and its home region.
2. An OCI API signing key configured for Terraform. Keep the private key outside this repository.
3. Terraform 1.6+.
4. An SSH public key (the private key stays on your device).

## Deploy

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your tenancy/user/compartment OCIDs and home region.
terraform init
terraform validate
terraform plan
terraform apply
```

Review the plan. Confirm every resource is Always Free eligible in the OCI Console before applying. Do not provision paid load balancers, GPU instances, NAT gateways, extra volumes, or managed databases on a free-only budget.

## Security and cost

- SSH is closed by default; only TCP/443 is allowed inbound.
- Configure TLS termination before exposing an application.
- Never commit OCI private keys, model API keys, database passwords, or device tokens.
- Use OCI instance principals and a narrowly scoped dynamic group/policy for Yuki after deployment. Terraform's local API signing key is only for provisioning.
- Use compartment quotas and budget/anomaly notifications where available. Free accounts can still incur charges if paid resources are created.
- The gateway device registry is not yet a complete HTTP gateway. Do not expose it directly to the internet.

## Not included yet

Application container deployment, DNS, certificate automation, gateway HTTP service, backups, model serving, high availability, and tested recovery. A single A1 VM is a single point of failure.
