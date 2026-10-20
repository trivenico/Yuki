# DarkChocolate — initial vertical slice

A local-first Python assistant prototype demonstrating a defensive shell around a bounded model/tool loop.

## Install
Python 3.10+:
- Unix: run sh install_unix.sh
- Windows: run install_windows.bat

## Run
Start Ollama locally and pull a model such as llama3.1:8b, then run python run_dark_chocolate.py.

## Implemented
Local Ollama chat, heuristic prompt inspection, plan proposal approval, bounded ReAct-style loop, tool registry with risk checks, local JSONL audit, bounded working memory, JSON knowledge graph with Obsidian export, mood prompt, and workspace-scoped report writing.

## Not yet implemented
ChromaDB, multi-step planner, durable task queue, daemon manager, face authentication, MCP client/discovery, terminal, authorized-target pentest tools, IDS/firewall integrations, browser/email, screen reader, STT/TTS/wake word, Textual dashboard, kill switch, and nightly evolution. Do not treat these as available features.
