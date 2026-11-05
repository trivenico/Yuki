# Phase 177 — Free GPU provider research and operating limits

Research date: 2026-11-05. Provider quotas and terms change; re-check at
submission time. This is a provider-selection reference, not a guarantee of
available capacity or an endorsement.

## Candidate providers

| Provider | Free capacity evidenced in current official sources | Good Yuki use | Main constraint |
|---|---|---|---|
| Google Colab | Free-of-charge notebook access can include GPUs and TPUs | Small LoRA/QLoRA experiments, profiling, evaluation | Resources are not guaranteed or unlimited; usage limits fluctuate |
| Kaggle Notebooks | Free GPU/TPU notebook accelerators; published guide describes P100 and T4 x2 configurations | Reproducible experiments and benchmark batches | Weekly GPU quota, availability queue, session limits |
| Hugging Face ZeroGPU | Shared RTX Pro 6000 Blackwell; free account daily quota documented as 5 GPU minutes | Very short inference/demo and smoke tests | Tiny free quota; not long training |
| BOINC Central | No-charge volunteer compute for eligible research | Independent public-data scientific tasks | Must be open-source, scientific/non-profit, public non-sensitive data and public results; not private commercial model training |
| AIs@home | Free daily quota to access volunteer GPUs; open-source, non-commercial experiment | Non-sensitive public prompt/inference experiments | Experimental, non-commercial, no confidentiality; volunteer node may technically read prompts |

## Source notes

- Colab FAQ: free GPU/TPU access exists, but resources are not guaranteed,
  unlimited, and limits fluctuate:
  https://research.google.com/colaboratory/intl/en-GB/faq.html
- Kaggle Notebooks docs describe free GPU/TPU access, availability queues,
  notebook session resources and weekly GPU quota:
  https://www.kaggle.com/docs/notebooks
  https://www.kaggle.com/docs/efficient-gpu-usage
- Hugging Face ZeroGPU docs list RTX Pro 6000 Blackwell allocation and free
  account daily quota of five minutes:
  https://huggingface.co/docs/hub/en/spaces-zerogpu
- BOINC Central requires scientific/non-profit goals, open-source software,
  public results and non-sensitive public input data:
  https://boinc.berkeley.edu/central/scientist.php
- AIs@home describes itself as an early non-commercial volunteer GPU
  experiment. Its privacy page warns that a volunteer node can technically
  read prompts:
  https://aisathome.org/
  https://aisathome.org/about/privacy/
  https://aisathome.org/about/terms/

## Yuki provider policy

- Never represent free capacity as guaranteed, persistent, or unlimited.
- Never bypass quotas, anti-abuse rules, identity checks or provider terms.
- Never launch paid compute or consume credits without explicit approval.
- Never send private chats, user memory, credentials, proprietary datasets or
  screen images to volunteer/community GPUs.
- For community nodes, use only synthetic/public non-sensitive prompts and
  verify provider terms and model license.
- Use resumable checkpoints, short jobs, immutable manifests and independent
  artifact validation.
- Treat volunteer output as untrusted: verify checksums, reproduce key
  evaluations and require human release approval.
- Use free tiers for experiments; use an approved institutional grant,
  volunteer research programme or owned worker for longer private workloads.

Zero-cost high-end GPU compute is an opportunistic source, not a production
capacity guarantee. Yuki's local CPU mode must remain functional when every
remote free tier is unavailable.
