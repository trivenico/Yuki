# Yuki

Yuki is an early-stage personal AI-agent prototype for terminal workflows, with a future path toward desktop use.

## Current status

This repository contains a small, beginner-friendly Python prototype with:
- A terminal chat loop
- A provider interface and a mock provider for offline testing
- An explicit allow-list of basic tools
- Local task, memory, and learning-outcome logs
- Local-state and secret patterns excluded by `.gitignore`

**Important:** The default mock provider does not call an AI service. OpenAI, Anthropic, Gemini, and local-runtime entries are registry placeholders, not working integrations yet. No API keys or credentials are required to try the prototype.

## Requirements

- Python 3.10 or newer
- No third-party packages required

## Run

From the repository folder:

```bash
python yuki.py
```

Type `/help` inside Yuki to see commands. Try:
- `/status`
- `/tool calculate 12 * (3 + 4)`
- `/task add Test Yuki`
- `/task list`
- `/memory add My first note`
- `/learn Organize files | success | Group related files before reviewing them`
- `/lessons Organize files`
- `/quit`

## Safety and privacy notes

- Tools are explicitly allow-listed; arbitrary shell commands and arbitrary Python execution are not exposed.
- The calculator accepts only basic arithmetic expressions.
- Memory, tasks, and learning history are stored locally in JSON files when the program runs. These runtime files are ignored by Git.
- Do not paste API keys, passwords, private customer data, or other secrets into prompts or notes.
- The learning log records outcomes and retrieves related lessons; it does not train or silently modify the underlying AI model.
- Real provider integrations, secure credential storage, browser automation, approval gates, audit logs, and a graphical interface are future work.

## Development direction

The project is intended to grow toward a provider-independent personal agent with explicit permissions, user approval for sensitive actions, clear audit trails, and user-controlled local data.
