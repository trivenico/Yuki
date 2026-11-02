# Ollama Conversation End-to-End Contract

The deterministic integration test starts a loopback HTTP server that mimics
Ollama's model-tag endpoint and NDJSON generation endpoint. It exercises the
real OllamaProvider, ConversationRuntime, ConversationService, and SQLite
session store together without downloading a model or calling an external
service.

The success case verifies incremental deltas and exactly one persisted
assistant message after the provider completion marker. The truncated-stream
case verifies provider failure is audited and partial assistant content is
not persisted. These tests validate the integration contract, not inference
quality, GPU performance, or compatibility with every Ollama release.
