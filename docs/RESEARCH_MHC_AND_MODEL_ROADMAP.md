# Research note: DeepSeek mHC and implications for Yuki

Date reviewed: 2026-11-17

## Sources reviewed

- Paper: Xie et al., mHC: Manifold-Constrained Hyper-Connections, arXiv:2512.24880v2 (2026-01-05), https://arxiv.org/html/2512.24880v2
- PDF: https://arxiv.org/pdf/2512.24880
- Reference implementation: https://github.com/Chenhao-Guan/deepseek-mhc

## Executive decision

**Do not add mHC to Yuki's production conversation, memory, agent orchestration, or desktop runtime.** It is a neural-network training architecture, not an application-level agent-routing or multi-agent coordination technique. Preserve it as a candidate experiment for Yuki's future first-party model-training stack.

The near-term priority remains completing and validating Yuki's core owner-facing assistant: reliable conversation runtime, provider lifecycle and bounded timeouts, private persistent storage and recovery, portable encrypted memory, safe backup/restore, and desktop startup/shutdown. An architecture experiment must not delay those deliverables.

## What the paper contributes

Standard residual blocks propagate a hidden state through an identity path plus a learned block. Hyper-Connections expand this into multiple residual streams and learn pre-, post-, and residual mappings. Unconstrained residual mappings can compound across depth, causing signal/gradient amplification or attenuation.

mHC constrains the residual-stream mixing matrix to the Birkhoff polytope: entries are non-negative and every row and column sums to one. The paper uses Sinkhorn-Knopp normalization to project learned scores onto this doubly-stochastic set. The set is closed under matrix multiplication and its matrices are non-expansive in spectral norm, providing a useful stability property while allowing streams to mix. The paper also constrains pre/post mappings to non-negative sigmoid outputs.

The paper's systems work matters as much as the mathematical constraint: it describes kernel fusion, recomputing selected mHC activations during backpropagation, and overlapping communication with computation in pipeline-parallel training. Those optimizations are specialized to large-scale model training and cannot be assumed to make a small model faster.

## Reference repository audit

The reference repository advertises PyTorch Sinkhorn-Knopp projection, dynamic and static mapping modes, ResNet integration, activation-recomputation support, and MIT licensing in its README.

Important limitations discovered:
1. GitHub repository metadata reports no detected license, and the repository tree does not expose a LICENSE file. The README's MIT statement is not sufficient evidence of a complete, auditable license grant for copying the code. Do not vendor or redistribute its implementation until the maintainer supplies a verifiable license file.
2. Its own README reports 78.02% CIFAR-10 accuracy for its tested ResNet-18-mHC configuration versus an approximately 94% standard ResNet reference, and explicitly warns that its implementation may need refinement and that the paper's results are on large MoE language models.
3. Its implementation notes explicitly describe a simplified architecture: convolutions occur before stream splitting. This is not a faithful reproduction of the paper's transformer residual-stream formulation.
4. The supplied Sinkhorn implementation uses exponentiation and alternating row/column normalization, but clamps logits and adds epsilon directly to denominators. A production implementation should be evaluated in log-space or with carefully bounded numerics, with convergence/error metrics, gradient tests, dtype coverage, and adversarial input tests.
5. The repository's RecomputeContext is a no-op context manager, so it does not itself implement activation checkpointing. The simplified get_optimal_block_size formula is not the paper's stated approximation, which is approximately sqrt(nL/(n+2)).
6. The code's dynamic mapping uses pooled image features and is not the paper's full flattened normalized hidden-state parameterization.

These findings make the repository useful as an educational reference, not a production-ready model component to transplant into Yuki.

## Potential Yuki applications — only after core completion

1. First-party model research: benchmark a small transformer with standard residual connections against an mHC variant. Keep the baseline intact and make mHC opt-in by model config.
2. Training stability instrumentation: record per-layer residual-stream row/column sums, minimum mapping entry, convergence residual, spectral norm estimate, activation RMS, gradient norm, loss spikes, throughput, and peak memory.
3. Controlled ablations: compare 1, 2, and 4 streams; static versus dynamic mappings; Sinkhorn iteration counts; baseline versus mHC; and at least three seeds. Evaluate language-model validation loss and downstream tasks, not CIFAR-10 alone.
4. Systems optimizations: only implement checkpoint/recompute or fused kernels after a correct reference implementation is validated and profiling proves the overhead is material.
5. Training infrastructure: keep training code and dependencies isolated from the desktop runtime. Kaggle GPU notebooks can be used for reproducible experiments; do not add PyTorch as a required dependency of the everyday Yuki desktop app.

## Acceptance gates before adoption

- Independently implement from the paper or use a source with a verified compatible license; preserve citations and notices.
- Unit tests prove non-negativity, row and column sums within a declared tolerance, finite outputs/gradients, identity behavior at one stream, and closure under matrix multiplication.
- Compare FP32, BF16/FP16 where supported, extreme logits, small/large stream counts, and convergence under bounded iteration budgets.
- Reproduce a baseline-vs-mHC experiment on the same data, token budget, optimizer, seeds, and compute budget.
- Report validation loss, task metrics, throughput, memory, and stability; require a measurable improvement before default adoption.
- No effect on production assistant startup, package size, provider selection, conversation correctness, or desktop reliability.

## Separate core priority: portable encrypted memory

Yuki currently encrypts memory with AES-256-GCM and stores its key in the operating-system credential store. The key is not included in the passphrase-encrypted backup archive, so a restore on a different OS account or computer cannot decrypt memory. This is an explicit portability limitation, not a reason to export the raw key beside profile files.

The next implementation must be designed around these constraints:
- Generate a random per-profile data-encryption key (DEK); do not share one global key across profiles.
- Keep the active DEK in the OS keyring for normal operation.
- For a portable backup, wrap the DEK using a key derived from the user-supplied backup passphrase with a memory-hard KDF and a unique salt; bind profile ID, key version, and purpose using authenticated metadata/AAD.
- Keep wrapped key material inside the already encrypted backup archive. Never stage a plaintext DEK on disk.
- On restore, verify the backup and memory ciphertext before importing. Require explicit owner intent to import the portable key; install it into the destination OS keyring under a profile-scoped key identifier.
- Define rollback behavior if keyring installation or filesystem publication fails. Never silently replace an existing profile key; collisions require an explicit safe recovery path.
- Preserve legacy same-device backups and existing memory ciphertext. Version the envelope and backup manifest; provide migration and downgrade behavior.
- Add tests for wrong passphrase, tampering, profile-key collision, missing keyring, keyring write failure, interrupted restore, duplicate restore, cross-device restore, and no plaintext key leakage.
- Do not implement until the key lifecycle, transaction ordering, and failure recovery are reviewed.

## Scope decision

No mHC code or PyTorch dependency is added to the production runtime in this change. The immediate engineering work remains portable-memory key lifecycle and end-to-end desktop profile restore validation, followed by a clean-machine smoke test of the core assistant.