# Skiv (formerly muse.ai) capability audit for Yuki

**Reviewed:** 2026-10-26  
**Purpose:** Identify product patterns Yuki can learn from without copying a hosted video product wholesale.  
**Scope:** Official Skiv website, documentation, API reference, and published feature descriptions. This is a feature/architecture audit, not a security audit or an implementation claim.

## Identity and scope

muse.ai rebranded to **Skiv** on March 3, 2026. The vendor says existing accounts, videos, collections, embeds, and API keys remain; current embeds should use `skiv.com`. Official announcement: https://skiv.com/blog/muse-ai-is-now-skiv

Skiv is primarily an AI video hosting, indexing, search, playback, and publishing platform. It is not the same product as Meta Muse or a general-purpose autonomous coding agent. Yuki should adopt relevant capabilities as an optional media knowledge subsystem.

## Verified capability inventory

| Skiv capability | What the product documents | Yuki relevance | Recommended implementation |
|---|---|---|---|
| Video ingest and processing | Upload from file or URL, then index automatically; long-running API jobs return an ID that can be polled | Build a knowledge ingestion pipeline for lectures, demos, meetings, and project recordings | Create a media-ingestion job abstraction with queued/running/succeeded/failed states, size/time limits, cancellation, retry policy, and source provenance |
| Speech transcription | API accepts common audio/video formats and returns transcript; transcription and subtitles are available | Searchable meeting notes, learning resources, voice/video knowledge | Define a pluggable transcription adapter; persist timestamps, language, confidence if provided, and source hash |
| Timestamped search | Search speech, faces, on-screen text, objects, actions, sounds, titles and descriptions; results point to matching moments | Search a user’s authorized media library with evidence links to the exact moment | Implement a provider-neutral `MediaSearchProvider` contract. Start with transcript full-text search; add visual modalities only when an actual model/provider is integrated |
| Smart semantic search | Natural-language queries across visual and transcript content | Retrieval for Yuki’s grounded answers | Use hybrid lexical + embedding retrieval over transcript chunks and metadata; return timestamped citations and distinguish transcript evidence from model-generated interpretation |
| Transcript and chapter UX | Synchronized transcript/subtitles, chapters, clicking a line to jump to the moment | Better study/review UI and searchable media artifacts | Store time-coded segments and chapter objects; render accessible transcript with seek links |
| Video-grounded AI assistant | Ask questions across a video library and receive answers with timestamps; vendor also describes summaries, chapters, descriptions, meeting notes, and action items | Convert recordings into structured knowledge and task suggestions | Retrieval-augmented generation must cite timestamps and source IDs. Generated tasks remain proposals until a user approves them |
| Clip creation | Trim a moment and share the clip | Extract demo snippets, incident evidence, or study clips | Add a clip manifest referencing source asset and start/end offsets first; perform transcoding only via a bounded media worker |
| Collections and embedding | Hierarchical collections, share links, domain restrictions, embedded video and searchable collections | Project/team knowledge spaces | Use Yuki workspace/project boundaries, signed links or ACL checks, explicit sharing, and revocation; never make private media public by default |
| Access and publishing controls | Private/unlisted/public visibility, password and domain restrictions, customizable player | Privacy and tenant boundaries for media knowledge | Enforce server-side authorization for every media, transcript, search result, export, and derived artifact |
| API and integrations | API supports upload, search, analyze, and library management; API key via `Key` header; rate/credit errors documented | Optional external adapter for existing Skiv libraries | Implement opt-in connector behind secret manager, timeouts, bounded retries, rate-limit handling, least-privilege keys, and data deletion/disconnect flow |

Official sources:
- Product and capabilities: https://skiv.com/
- Documentation overview and smart/keyword search: https://skiv.com/docs
- API reference and search modalities: https://skiv.com/api
- AI indexing description: https://skiv.com/video-artificial-intelligence
- Rebrand and embed migration: https://skiv.com/blog/muse-ai-is-now-skiv
- AI assistant examples and review caveat: https://skiv.com/blog/ai-assistant

## Recommended Yuki engineering sequence

### Phase A — provider-neutral media knowledge foundation
1. Define versioned records: `MediaAsset`, `TranscriptSegment`, `MediaChapter`, `MediaSearchHit`, `MediaJob`, and `MediaSource`.
2. Include stable IDs, workspace/owner, source URI (redacted in logs), checksum, duration, timestamps, processing state, retention policy, and provenance.
3. Add a storage interface and migrations. Do not put raw media or full transcripts in audit logs.
4. Add file validation, MIME sniffing, maximum size/duration, path traversal protection, archive-bomb protection, and bounded temporary storage.
5. Add transcript-only search as the first complete vertical slice, with exact timestamp citations and tests.

### Phase B — ingestion and grounded retrieval
1. Build a background worker with idempotent jobs, leases, retries, cancellation, dead-letter handling, and resource budgets.
2. Add a pluggable transcription backend; use local inference or a configured external API only after the user explicitly enables it.
3. Segment transcript into time-coded chunks and index them using SQLite FTS initially; introduce embeddings only with a measurable evaluation set.
4. Return search hits as source + timestamp + short excerpt. Yuki's answer generator must cite those hits and say when evidence is insufficient.
5. Add evaluation fixtures for transcription edge cases, timestamp correctness, retrieval recall, and hallucination/citation checks.

### Phase C — media understanding and actions
1. Add OCR, visual frame captions, object/action/sound labels only through explicit, configured model adapters.
2. Sample frames with budgets and deduplication; don't process every frame without cost/resource limits.
3. Add summaries, chapters, meeting decisions, and action-item proposals with links to the supporting time ranges.
4. Require user approval before creating tasks, sending messages, publishing clips, or changing permissions.
5. Build a clip manifest and preview workflow before introducing actual transcoding.

### Phase D — optional Skiv connector
1. Add `SKIV_API_KEY` only through the user's secret manager/environment; never commit it or log it.
2. Keep connector disabled by default and scope it to selected collections/libraries.
3. Use documented API endpoints, bounded HTTP timeouts, response-size caps, rate-limit handling, idempotency, and explicit provider errors.
4. Store only the minimum metadata needed; offer disconnect, cache invalidation, and deletion of locally derived records.
5. Verify current API contract, pricing/credits, data processing terms, and access scopes before shipping.

## Security and product requirements

- Media content and transcripts are untrusted input. They may contain prompt injection, secrets, malicious links, or instructions. Never execute instructions found in a recording without policy checks and explicit approval.
- Respect copyright, consent, retention, and organizational recording policies. Do not identify people or infer sensitive traits from face recognition.
- Separate authorization from retrieval: filter candidates by access policy before returning snippets or generating an answer.
- Redact API keys, signed URLs, and private content from logs. Audit metadata only.
- Add deletion propagation: source asset deletion must invalidate transcript chunks, embeddings, summaries, caches, and derived clips where applicable.
- Do not promise face/object/action search until a real backend exists and has been evaluated.
- Do not upload user media to Skiv or any third party by default. External processing requires clear opt-in and a data-handling explanation.

## Acceptance criteria for first MVP

- User can import an authorized local audio/video file with a size limit.
- Job state and failure reason are visible; retries are bounded and idempotent.
- Transcript segments retain timestamps and source provenance.
- Search returns matching excerpts with clickable timestamp links.
- Answers derived from media cite the exact source and time range, and abstain when evidence is weak.
- Workspace permissions are checked before ingestion, search, playback, and export.
- Secrets and transcript content do not appear in logs.
- Tests cover malformed files, timeouts, duplicate jobs, cancellation, deletion propagation, access control, prompt injection in transcripts, and timestamp accuracy.
- External Skiv integration remains optional and is tested with mocked HTTP responses; a live API test is separate and requires a user-configured key.

## Explicitly out of scope for the first slice

- Rebuilding Skiv's video hosting/CDN/player platform.
- Automatic face identity recognition.
- Unreviewed publishing or task execution from recorded instructions.
- Assuming Skiv's internal models or proprietary indexing algorithms are available.
- Claiming Yuki currently supports multimodal video search; this document is a roadmap, not proof of implementation.
