# Gate-to-task exec transition observation

The host-side `LinuxGateExecTransitionObserver` supplements the transport ACK.
The ACK proves only that the image-owned gate verified its signed, one-shot
authorization envelope. It does not prove that `execvpe` succeeded.

After authorization delivery, a host supervisor may use the observer with the
daemon-inspected gate PID, the host-visible UID already bound to the listener,
and the exact argv from the signed authorization. The observer:

- records the process start time before polling;
- requires the process UID credentials to remain consistent and unchanged;
- reads the host procfs command line and compares the complete argv exactly;
- checks process start time around each observation to reject PID reuse;
- returns true only after observing the exact task argv in the same PID;
- returns false on bounded timeout, and fails closed if the process exits or
  becomes unreadable before confirmation.

A false result is **unconfirmed start**, not evidence that the task did not run.
Very short-lived tasks may exit before the observer samples them. The caller
must not turn an ACK or a timeout into a successful-start event. Container
wait/exit status remains a separate lifecycle observation.

This helper is not yet wired into a production gate-first executor lifecycle.
The existing direct Docker executor remains unchanged. Rootless/userns UID
mapping, Docker create/start orchestration, lease-loss cancellation, bounded
output handling, and guaranteed container cleanup still require integration
and deployment verification.
