# Phase 176 — Screen awareness and zero-cost compute strategy

## Screen awareness: trust boundary

Yuki may observe a screen only under an explicit, short-lived, device- and
session-bound user grant. SSH credentials alone are not consent to capture a
desktop. The grant separately controls observation, sensitive-content
inclusion, remote input and file transfer. All powerful permissions default
off. The initial policy limits grants to one hour and image payloads to 32 MiB.

The policy module validates authorization before an adapter captures pixels.
A future adapter may use:
- Local desktop capture through a platform-specific permissioned API.
- SSH to invoke a narrowly scoped, preinstalled screenshot helper on a
  headless remote host, only after that remote host has an active grant.
- Existing remote desktop protocols where the user explicitly shares the
  session.

OCR and vision processing should prefer local execution. Screen images may
contain passwords, private messages, financial data, health data, API keys or
other secrets. Apply sensitive-region redaction by default, never persist
screenshots by default, avoid logging pixels, and offer pause/revoke controls.
Do not capture lock screens, password fields or secure desktops.

Remote clicking, typing, shell commands and file transfer are separate
permissions and must not be inferred from screen-observation consent. Require
per-action confirmation for destructive or externally visible actions.

## Free/community GPU options: research snapshot (2026-11-05)

- Google Colab free tier: free GPUs/TPUs, but resources are not guaranteed;
  quotas and hardware availability fluctuate, with idle timeouts and session
  lifetime limits. Appropriate for experiments and short fine-tuning runs.
- Kaggle Notebooks: free GPU/TPU notebook accelerators subject to availability
  and quotas; suitable for reproducible short jobs and benchmark experiments.
- Hugging Face ZeroGPU: shared RTX Pro 6000 Blackwell GPUs, but free accounts
  currently have a small daily quota (documented as five minutes); suitable
  for demos and brief inference, not long training.
- BOINC: open-source volunteer computing infrastructure, but projects are
  independently operated. BOINC Central accepts qualifying open-source,
  non-profit scientific research with public non-sensitive data; it is not a
  general free private commercial training cluster.
- AIs@home: an emerging volunteer GPU inference experiment, currently
  non-commercial. Treat as research/experimental, not production capacity.
- Standard Hugging Face Spaces CPU Basic is free, but GPU hardware upgrades
  are paid. Do not confuse free CPU hosting with free dedicated GPU.

No option guarantees unlimited, persistent, high-end GPU compute for arbitrary
training. Yuki must never evade quotas, create multiple accounts to bypass
limits, or run long jobs under an interactive notebook in violation of terms.

## Zero-cost training plan

1. Run data cleaning, validation, unit tests and small CPU experiments locally
   or in free CI.
2. Use free notebook/ZeroGPU quotas for short profiling, smoke tests,
   evaluation slices and small adapter experiments.
3. Save checkpoints and logs to user-controlled durable storage frequently.
4. Schedule long runs only when a legitimate research grant, institution,
   volunteer project or explicitly approved free allocation exists.
5. Track provider quotas, terms, deadlines, hardware, persistence and data
   residency. Fail closed when quota/budget is unknown; never start paid
   compute without explicit approval.
6. Do not upload private user memory, conversations or screen captures to
   community GPUs. Use synthetic/public/licensed training data only unless
   users separately authorize transfer.

## Implementation status

The screen observation policy is implemented as an authorization boundary;
there is not yet a desktop capture, SSH screenshot transport, OCR, vision
pipeline or remote-control implementation. Those require separate adapters,
platform permissions and security review.
