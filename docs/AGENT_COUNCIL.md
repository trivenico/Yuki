# Yuki Agent Council

Yuki can convene a small, bounded advisory council before planning a task. It is designed to feel like a focused engineering board meeting: independent specialists examine the same goal in parallel, then a chair compares their recommendations and produces a synthesis.

## Default roles

- **Strategist** — decomposes the goal, identifies dependencies, and prioritizes milestones.
- **Implementation engineer** — proposes practical interfaces, implementation steps, and verification criteria.
- **Independent critic/security reviewer** — challenges assumptions, highlights failure modes, and recommends safer alternatives.
- **Council chair** — compares agreement and disagreement across the available responses and produces a prioritized plan.

The three specialist calls run concurrently. Each specialist then cross-reviews the other available proposals in a second parallel round. The chair compares the original analyses and peer critiques and makes one synthesis call. If the task is a /task command, the synthesis and specialist notes are passed to the existing bounded task planner as untrusted advisory context.

## Commands

- /council status — shows whether the council is enabled for automatic task planning.
- /council run GOAL — explicitly convenes the council and prints its synthesis and available specialist contributions.
- /task GOAL — when enabled, convenes the council before the bounded task planner begins.

## Settings and resource use

The council is enabled by default for task planning. Disable **Settings → AI Agent Council → Enable parallel Yuki Council for task planning** to reduce latency, local model load, or provider usage. The setting can also be controlled with agent_council_enabled or YUKI_AGENT_COUNCIL_ENABLED.

One full council round uses up to seven model generations: three parallel first opinions, three parallel peer critiques, and one chair synthesis. Failed or timed-out calls may reduce this count. Each specialist has bounded input/output, and the council uses the configured provider only. With local Ollama, several concurrent requests may compete for RAM/VRAM; disable the setting if the local model becomes slow or unstable.

## Safety boundaries

The council is advisory. It has no tools, filesystem access, shell access, permission-granting ability, or upgrade privileges. Goal text and model responses are untrusted. The existing task agent remains responsible for strict action parsing, registered-skill allow-listing, fresh human approval for each skill invocation, step limits, task tracking, and verification. Council recommendations do not count as authorization.

If specialist responses fail, Yuki reports a partial result or falls back to the bounded single-agent planner. A council summary is not proof that a task was performed or succeeded. Use the task ledger and independent verification steps for completion.

This is a small local council, not four separate pretrained foundation models. All roles currently use the configured provider/model with different role prompts. Multi-model routing, durable debate transcripts, explicit disagreement scoring, and a visual live meeting-room view remain future enhancements.
