# Conversation import adapters

The first adapter milestone adds safe normalization for:

- ChatGPT `conversations.json` exports.
- Yuki's provider-neutral normalized JSONL format.

## Safety and consent boundary

These parsers accept explicitly supplied UTF-8 bytes or text, enforce a 25 MiB input limit, a 2,000-record limit, and a 12,000-character text limit. They extract supported text only; image, audio, video, and other non-text payloads are ignored. They do not read archives, follow links, execute content, persist data, or imply user consent.

The application must show a preview and obtain explicit user approval before passing normalized records to `ConversationMemoryStore.import_records(..., consent=True)`.

## ChatGPT export

Pass the decoded contents of the `conversations.json` file from a user-provided ChatGPT export to `parse_chatgpt_export`. It reads text messages from the export's conversation mapping.

## Provider-neutral JSONL

Each non-empty line is an object with `id`, `role`, `content`, and optional `created_at`. Content may be a string or an array of strings / text objects.

## Not yet supported

Claude, DeepSeek, Kimi, GLM, and other exports have distinct schemas and must receive separately tested adapters. Generic JSONL is available as an explicit conversion format; it is not claimed to parse those vendors' native archives. ZIP extraction, desktop upload/preview UI, CLI commands, semantic retrieval, and model fine-tuning remain separate milestones.
