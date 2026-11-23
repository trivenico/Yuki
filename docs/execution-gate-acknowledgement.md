# Execution-gate acknowledgement semantics

The private transport has two distinct phases:

1. The gate receives a bounded frame and validates the host peer UID and exact transport schema. Receiving bytes is not an acknowledgement.
2. The gate verifies the signed authorization envelope, expiry, nonce, and exact task/worker/container/spec/image binding. Only after all checks pass does it send the one-byte transport acknowledgement.

The host listener's successful return therefore means only that the expected gate process acknowledged a cryptographically valid authorization. It does **not** prove that execvpe succeeded, that the task process reached its intended executable, or that task work began.

The gate closes the transport connection before attempting execvpe. The host-side launcher must separately monitor the daemon-assigned container's state and process identity/exec transition, and must treat missing or ambiguous evidence as failure. The current collector callback and listener acknowledgement are not a task-start monitor. No production gate-first executor is activated by this transport change.

Invalid authorization, expiry, replay, binding mismatch, or acknowledgement failure prevents the gate from reaching execvpe. The host must stop and remove the container on release failure, lease loss, cancellation, or timeout.
