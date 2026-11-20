# Provider streaming reliability

Yuki's OpenAI-compatible and Ollama streaming adapters use the configured provider timeout when opening the HTTP response. With Python's synchronous `urllib` transport, the socket timeout is an **idle/read timeout**, not a hard wall-clock deadline for the entire response. A peer that continues sending data before each idle timeout can keep a stream open longer than that timeout.

The agent execution loop separately enforces its configured elapsed-duration budget between received stream events. Consequently, cancellation and duration enforcement are responsive between events, but cannot pre-empt a currently blocked synchronous `readline`; the transport read timeout bounds that wait when the peer stops sending.

Consumers must close a stream generator when abandoning it. Closing the generator exits the response context manager and closes the underlying HTTP response. The provider stream tests cover this cleanup for both SSE and NDJSON adapters.

A future hard wall-clock cancellation guarantee requires an interruptible transport or isolated I/O worker with bounded lifecycle and cleanup. Do not describe the current synchronous implementation as providing that guarantee, and do not add unmanaged background threads as a workaround.
