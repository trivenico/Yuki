# Interactive CLI Conversations

Run \`python yuki.py\`, then enter \`/chat\` to start or resume the newest
active local conversation. Type a message to stream a response from the
provider manager. Use \`/back\` to return to the existing Yuki command loop.

Conversation events are stored in \`~/.yuki/conversations.sqlite3\`, or under
the directory selected by \`YUKI_DATA_DIR\`. The directory is restricted to
the current user where the platform supports POSIX permissions. The CLI uses
a local actor identity and a local authorization callback; this is not a
multi-user authentication boundary. Cloud routing is disabled by default.
The CLI does not yet implement asynchronous keyboard cancellation while a
provider is streaming; closing the runtime iterator is supported by the
underlying runtime and transport.
