# Conversation SSE completion sentinel queue overflow audit

**Severity:** P2 — stream availability / request completion  
**Status:** Confirmed by source inspection  
**Affected component:** `api/conversations.py`, `ConversationAPI._stream_reply`

## Summary

The conversation SSE producer can lose its terminal sentinel when the bounded event queue is full. The consumer then waits forever on `events.get()`, even though the generation worker has exited and its task has completed.

## Evidence

The stream creates a bounded queue:

```python
events = asyncio.Queue(maxsize=64)
```

Provider text deltas are published from the worker thread. `publish()` schedules an `enqueue()` callback with `loop.call_soon_threadsafe`. The callback bounds queue growth by clearing a full queue and inserting a `stream_consumer_too_slow` error event.

However, the worker's `finally` block schedules terminal insertion independently:

```python
loop.call_soon_threadsafe(events.put_nowait, sentinel)
```

That callback does not use the overflow handling in `publish()` and does not wait for queue capacity. If 64 events are queued when it runs, `Queue.put_nowait` raises `asyncio.QueueFull` on the event-loop thread. The sentinel is not inserted.

The consumer loop exits only after receiving the sentinel:

```python
event = await events.get()
if event is sentinel:
    break
```

After it drains the existing events, it blocks on the next `events.get()`. The worker task has already completed, but the consumer does not await it until after the sentinel is received. Therefore the HTTP response can remain open indefinitely.

The existing `tests/test_conversation_api.py` covers normal delta and completed-event emission, but the inspected test does not exercise a full queue at worker termination.

## Impact

A sufficiently fast producer combined with a slow client can fill the 64-event queue at the exact point the worker schedules termination. The client may receive some deltas or a slow-consumer error but never receive a terminal event or final response-body completion. This can retain an HTTP request/task and client-side stream resources until an external disconnect or server timeout intervenes.

This is not an unbounded-memory issue: the queue is bounded. The defect is loss of a required control event under backpressure.

## Recommended remediation

Make terminal delivery capacity-aware and non-droppable:

1. Use an async queue-put coroutine scheduled thread-safely (for example, `asyncio.run_coroutine_threadsafe(events.put(sentinel), loop)`) so terminal insertion waits for capacity while the consumer drains.
2. Ensure the worker/consumer lifecycle cannot deadlock if the consumer has disconnected or the event loop is shutting down; cancellation and loop-closed handling should be explicit.
3. Alternatively, use a separate completion signal (such as a thread-safe event/future) that cannot be displaced by ordinary delta events, and have the consumer wait on both queue activity and worker completion.
4. Preserve the current bounded queue and slow-consumer policy; do not solve this by making the queue unbounded.

## Regression tests

- Fill the event queue to capacity immediately before worker finalization; assert the consumer still terminates.
- Exercise a producer emitting more than 64 deltas while the send side is deliberately delayed.
- Assert the stream emits a terminal error/completion outcome and sends the final `more_body=False` frame.
- Exercise disconnect while terminal delivery is blocked; assert cancellation and worker cleanup complete without an unobserved task.
- Repeat the test enough times to cover scheduling interleavings deterministically using controlled barriers rather than timing-only sleeps.

## Scope and limitations

This finding is based on source inspection at PR #1346 head `9346f2ad37b202824e5152cf499af48626b47c5d`. No runtime reproduction or test execution was performed. The finding is limited to the conversation SSE path; the separate inference API currently inspected uses ordinary JSON responses rather than this SSE queue.
