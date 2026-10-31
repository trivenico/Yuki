# Phase 102 — agent-native CLI events

## DeepTutor reference review

Reviewed the public HKUDS/DeepTutor repository and its current README/CLI documentation (Apache-2.0). The most transferable patterns for Yuki are:

1. One CLI usable by both humans and other agents.
2. A structured NDJSON event stream with correlation IDs.
3. A shared capability/runtime surface instead of separate logic for CLI and desktop.
4. Explicit workspace/resource selection and inspectable readiness (`doctor`).
5. Task/session continuity, resumable work and bounded memory.
6. Knowledge workflows that connect research, source retrieval and cited outputs.
7. Clear module boundaries and optional installation profiles.

Yuki already has a number of adjacent foundations: guarded CLI/desktop entrypoints, task ledger/runtime, skill registry, capability manifest, knowledge indexer/graph, local memory and specialist coordination. Avoid building parallel systems; future work should wire these through the existing registries and task lifecycle.

## Implemented in this phase

- Added `yuki run <prompt> --format json` as a machine-readable one-shot invocation.
- Added NDJSON `start`, `content`, `error` and `done` event records.
- Every event carries generated `session_id` and `request_id` correlation IDs.
- Content output is chunked at bounded Unicode-safe character boundaries.
- Event serialization rejects unknown event types, non-finite/non-JSON values and oversized records.
- Provider failures are represented as structured error and terminal events with non-zero exit status.
- Registered the feature as experimental in the evidence-backed capability manifest.

## Usage

```bash
python yuki_cli.py run "Summarize this project" --format json
```

Example event sequence (IDs shortened):

```json
{"type":"start","session_id":"s1","request_id":"r1","data":{"mode":"single_turn","tool_execution":"disabled"}}
{"type":"content","session_id":"s1","request_id":"r1","data":{"text":"Project summary..."}}
{"type":"done","session_id":"s1","request_id":"r1","data":{"status":0}}
```

## Deliberate limitations

- This is one-shot only. The generated session ID correlates events; it does not imply persistent/resumable conversation state.
- This path calls the configured provider's simple `generate` API; it does not invoke Yuki tools or specialist agents.
- Content is chunked after the provider returns a complete response; this is not token-level streaming.
- JSON events are transport records, never an authorization grant.
- The existing guarded runtime remains the path for approved tool execution.

## Recommended follow-on work

1. Add a durable conversation/session store and explicit `--session` resume semantics.
2. Adapt the existing guarded agent/task runtime to emit the same NDJSON event contract.
3. Add `yuki capabilities list`, `yuki doctor --format json`, and workspace/resource selectors.
4. Build a research capability on existing web/knowledge services with source IDs and citation evidence.
5. Add task-board and approval events without bypassing the existing approval digest and task ledger.
6. Expose the same runtime events to the desktop UI so CLI and desktop remain thin clients.

## Source and reuse notes

- Upstream: https://github.com/HKUDS/DeepTutor
- DeepTutor advertises Apache License 2.0. Yuki is MIT; retain upstream notices and review file-level licensing/dependencies before copying code. This phase adopts architectural patterns and does not copy DeepTutor source files.
- The upstream README is the source for the feature and CLI pattern review; verify individual upstream modules, dependency versions and license notices before any code-level reuse.