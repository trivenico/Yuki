# Phase 178 — Hugging Face compute billing clarification

Research date: 2026-11-05.

Do not classify Hugging Face Jobs as a generally free GPU training service.
Official Jobs pricing states that Jobs require a positive credit balance and
GPU hardware is billed by the minute. Example published rates include T4
small at $0.40/hour, L4 at $0.80/hour, A100 at $2.50/hour and H200 at
$5.00/hour. Rates and availability can change:
https://huggingface.co/docs/hub/jobs-pricing

Hugging Face ZeroGPU is a separate shared quota-based Spaces facility. Its
current free account quota is documented as five GPU minutes per day; it is
not equivalent to a persistent free Jobs worker:
https://huggingface.co/docs/hub/en/spaces-zerogpu

Yuki's provider registry must distinguish:
- `free_shared_quota`: shared quota, no guaranteed availability.
- `free_grant`: explicitly awarded grant, with documented scope and expiry.
- `volunteer_noncommercial`: community hardware with privacy/terms limits.
- `paid_metered`: explicit cost and approval required.
- `local_owned`: user's own hardware and electricity/network costs.

Never infer a free allocation from the provider name or a marketing phrase.
Check the exact product, hardware, account tier, remaining quota, storage,
egress, timeout, and billing state before submitting. Paid usage must be
disabled unless the user has explicitly enabled a budget and approved the job.
