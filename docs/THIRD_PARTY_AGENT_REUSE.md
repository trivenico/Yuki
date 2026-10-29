# Third-party AI agent reuse strategy

**Status:** Accepted for evaluation; no new runtime dependency approved yet  
**Owner:** Yuki core runtime  
**Last reviewed:** 2026-10-29

## Decision

Yuki may reuse and adapt permissively licensed open-source code when it removes meaningful engineering work, but must not wholesale-copy an agent repository into the core runtime. Prefer, in order:

1. Reuse interoperable protocols and formats already supported by Yuki (Agent Skills, MCP, OpenAI-compatible model APIs).
2. Integrate a mature project behind a narrow, optional adapter or subprocess boundary.
3. Port a small, well-isolated implementation only after license, provenance, security, and maintenance review.
4. Implement in-house only where Yuki's privacy model, permission checks, cross-platform packaging, or companion UX require it.

No third-party agent may bypass Yuki's authorization, workspace restrictions, audit trail, tool contracts, model-routing policy, or human approvals. A framework's own approval mechanism is not a substitute for Yuki's authoritative policy gate.

## Candidate assessment

| Candidate | Reuse opportunity | Risks / gaps | Initial decision |
|---|---|---|---|
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | Python/.NET multi-agent workflows, sequential/concurrent handoffs, human-in-the-loop, checkpointing, observability | Must verify provider compatibility with Yuki's local Ollama and current adapters; dependency and API surface may be substantial | **First general-orchestration spike.** Prototype outside the default runtime; compare against the existing task/workflow engine before adopting |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Stateful graph orchestration, checkpointing, resumable execution, human review | Adds LangChain-core and related dependencies; risks duplicating Yuki's SQLite ledger and workflow engine | Strong alternative; benchmark against Microsoft Agent Framework rather than adopting both |
| [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk) | Specialized coding agent, workspace abstractions, tools, event-driven execution, optional isolated Agent Server | Coding-focused, heavier stack, and requires careful workspace/sandbox integration; SDK's own tool access must be constrained | **Best candidate for an optional coding-agent adapter** after a sandbox proof of concept |
| [Open Interpreter](https://github.com/openinterpreter/openinterpreter) | Cross-platform terminal agent, model/provider switching, sandbox and permission concepts, MCP/skills | Current upstream is a Rust-based coding agent, not a drop-in Python library; subprocess packaging and permissions need evaluation | Study its harness and sandbox model; only integrate as a separately supervised optional process if it materially improves Windows support |
| [Continue](https://github.com/continuedev/continue) | Model-provider patterns, editor integrations, coding UX | Upstream README says the repository is read-only / no longer actively maintained | Reference or selective Apache-2.0 port only; not a strategic runtime dependency |
| [AutoGen](https://github.com/microsoft/autogen) | Historical multi-agent patterns | Upstream is archived and explicitly not recommended for production; successor is Microsoft Agent Framework | Do not adopt for new production runtime |
| Google Jules | Useful product/UX benchmark for delegated coding tasks and asynchronous progress | Product/service is not equivalent to a permissively licensed source repository that Yuki can copy | Benchmark behavior and integrate public APIs only where available and terms permit; do not copy proprietary implementation |

This is an architecture shortlist, not a claim that any candidate has passed Yuki's integration or security tests. Re-check upstream status, exact release/tag, repository license, and dependency licenses at the time of adoption.

## What we should reuse first

- **Skills:** continue using the Agent Skills format and Yuki's pinned-source/provenance checks. Installing a skill must not silently execute its scripts.
- **Tool interoperability:** keep MCP behind Yuki's existing host allowlist, HTTPS/DNS protections, approval policy, response limits, and audit events.
- **Model access:** retain Yuki's provider abstraction and explicit provider/model selection; do not let a framework introduce silent cloud fallback.
- **Coding tasks:** test OpenHands SDK in a separate workspace with no host-wide filesystem access and no network by default. Expose only Yuki-approved tools.
- **Durable orchestration:** build a small comparison spike for Microsoft Agent Framework vs. LangGraph vs. Yuki's existing workflow engine. Use the same acceptance suite and a no-network mock provider first. Do not run two independent task ledgers in production.
- **Desktop automation:** keep OS control in Yuki-owned capability adapters with explicit per-action approvals. Never grant an imported agent blanket shell, keyboard, mouse, camera, or screen access.

## Additional high-priority candidates: OpenClaw, Hermes, OpenJarvis, Dodaios

These projects were requested for direct evaluation on 2026-10-29. Their public repositories/docs were inspected for architecture and license claims; this is not a full source audit, vulnerability assessment, or proof that their latest builds run in Yuki.

| Project | Upstream / stated license | What is worth reusing | Integration recommendation | Main cautions |
|---|---|---|---|---|
| **OpenClaw** | [openclaw/openclaw](https://github.com/openclaw/openclaw) — MIT; review `THIRD_PARTY_NOTICES.md` for incorporated code | Channel/gateway architecture, provider and plugin registry boundaries, Agent Skills format, device-node concepts, security model | **Do not port the whole runtime.** Import a selected `SKILL.md` through Yuki's existing pinned GitHub skill importer. Later, evaluate a separately supervised gateway/MCP adapter if a concrete channel or device capability is needed. | Very large TypeScript/Rust/pnpm workspace, broad host integrations, high dependency footprint. Main-session tools may run on the host unless sandboxing is configured. MIT core license does not automatically relicense third-party components. |
| **Hermes Agent** | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — MIT per upstream repository | Python agent loop patterns, skills, memory/learning loop, tool gateways, messaging and provider patterns | **Best Python source for selective code review.** First reuse compatible skills. Then port only a small isolated component after exact-file provenance and dependency review; consider a supervised CLI/MCP adapter if its public interface is stable. | Self-improvement and tool execution are powerful attack surfaces; never allow generated skills or learned memory to grant permissions. Provider/model assumptions and dependencies need compatibility testing. |
| **OpenJarvis** | [open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis) — Apache-2.0 | Personal-device/local-first research, on-demand/scheduled/continuous agent patterns, benchmark/skill optimization, deep research and monitoring concepts | **Best benchmark and evaluation candidate.** Reuse its task/skill benchmark ideas and investigate a narrow optional Python adapter; compare its native ReAct/operative agents with Yuki's task lifecycle before importing implementation code. | Research project and broad optional capabilities may add dependencies. Any code-execution/CodeAct agent must run in an isolated workspace with no ambient credentials or host access. |
| **Dodaios** (formerly Personal Jarvis) | [Dodaios/Dodaios](https://github.com/Dodaios/Dodaios) — Apache-2.0 from 2.0.0 onward; releases through 1.6.0 remain MIT; follow `NOTICE` and licensing docs | Desktop/voice interaction, coding-agent panes, worktree isolation, agent coordination, approval UX, local memory and artifact workflows | **Best desktop-companion UX benchmark.** Study architecture and test the workflow concepts. Do not copy its desktop shell wholesale; compare the narrowest components against Yuki's Windows-first runtime and desktop launcher. | License changes by version; preserve the correct notices. Existing agent CLIs and tool backends have their own licenses and security boundaries. |
| **OpenHands Software Agent SDK** | [OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) | Coding agent workspaces, event-driven agent execution and tool adapters | Keep as the coding-specialist alternative to OpenJarvis's coding mode; benchmark both rather than integrating both immediately. | Workspace isolation and host tool permissions must be enforced by Yuki, not inferred from the SDK alone. |

### First actual code reuse: Hermes skill linter

Yuki now includes an adapted, deliberately bounded subset of Hermes Agent's advisory `tools/skill_linter.py`:

- Upstream file: https://github.com/NousResearch/hermes-agent/blob/a938f00f0f3cdf00fdb93d9a82a9ae447e9b857f/tools/skill_linter.py
- Upstream project license: MIT, copyright 2025 Nous Research; preserved at `third_party/licenses/HERMES_AGENT_LICENSE`.
- Yuki adapter: `core/skill_linter.py`; this is modified for Yuki's duplicate-key-rejecting frontmatter loader, CLI output, and bounded local file scans.
- CLI: `python yuki_cli.py skills lint path/to/SKILL.md`.
- Tests: `tests/test_skill_linter.py` and CLI tests in `tests/test_yuki_cli.py`.

The linter checks metadata quality, descriptions, trigger sections, shell-tool wording, oversized instructions, dangling local references, suspicious scaffolding files, portability declarations, and heuristic patterns associated with prompt overrides, secret transfer, remote-shell pipes, destructive root deletion, or approval bypass. These risk-pattern matches are advisory only, can produce false positives/negatives, and are not a malware detector. Hard installation validation remains authoritative. It never executes scripts, installs dependencies, or grants skills permissions. Ubuntu quality gates passed on a preceding code revision; CI for the latest exact head has not yet been confirmed, so this port is not yet fully cross-platform validated.

### Integration work that can be used immediately

Yuki already has a safer common denominator: `yuki skills add OWNER/REPO --path <skill-directory>` resolves a GitHub revision to a commit SHA, previews the selected skill, requires approval, and imports only its `SKILL.md` instructions. It does **not** copy or execute companion scripts. This is suitable for importing compatible skills from OpenClaw, Hermes, OpenJarvis, or other repositories after selecting a specific skill and reviewing its content.

Example (replace `<verified-skill-directory>` with a path confirmed from that repository's current tree):

```powershell
python yuki_cli.py skills add openclaw/openclaw --path <verified-skill-directory>
python yuki_cli.py skills add NousResearch/hermes-agent --path <verified-skill-directory>
python yuki_cli.py skills add open-jarvis/OpenJarvis --path <verified-skill-directory>
```

Do not run these commands unattended as a production bootstrap: the user should inspect the preview and approve the selected skill. The repository may contain no compatible skill, many skills, or a different path after an upstream update.

### Extension-store boundary hardening

A follow-up audit found that installed extension directories and JSON manifest paths could follow symbolic links. The extension store now rejects a symlinked installed_skills / installed_plugins directory, rejects symlinked manifest inputs, and requires manifest files discovered by loaders to be regular non-symlink files. Regression tests cover manifest-source symlinks, installed-manifest symlinks, and redirected store directories.

This is defense-in-depth for local filesystem boundaries, not a complete defense against a same-user attacker racing filesystem changes (TOCTOU). The desktop app still needs platform-specific review of data-directory ACLs, installer behavior, and permission prompts. CI for the current commit is pending.

### Proposed code-reuse sequence

1. **Now:** use the existing skill importer and tests; do not add a new runtime dependency just to consume markdown skills.
2. **First code-port candidate:** inspect Hermes for one small, well-bounded Python component (e.g. a pure parser or non-executing skill utility), then copy only after checking the exact source file, history, license header, transitive imports, and tests. If no component is independently useful, do not port code merely to claim reuse.
3. **First orchestration benchmark:** compare Yuki's current agent against OpenJarvis agent patterns and one of Microsoft Agent Framework or LangGraph. Use mock providers and a common task suite.
4. **Desktop/voice benchmark:** use Dodaios and OpenClaw as architecture/UX references; preserve Yuki's Windows-first packaging and explicit screen/camera/desktop permissions.
5. **Adapter decision:** only after evidence, add one optional adapter behind a narrow API. All external tool proposals must pass Yuki's authorization gate and write to Yuki's durable task timeline.

### Reuse is not a substitute for tests

No code from these four repositories has been copied into Yuki in this commit. Their licenses permit reuse subject to their terms, but a production port still requires exact-version/file provenance, notice retention, dependency/SBOM review, static/security analysis, tests, and an explicit decision about who owns updates. This is deliberate: bulk-copying several runtimes before establishing their boundaries would make Yuki harder to secure and ship, not faster.

## Mandatory intake checklist before copying or adding a dependency

1. Identify the exact upstream repository, immutable commit/tag, source file paths, and authorship.
2. Verify the file-level license and repository license. Check bundled assets, generated code, model weights, examples, and transitive dependencies separately.
3. Preserve required copyright/license notices; include license texts and attribution in the distribution where required. Record modifications and upstream provenance.
4. Generate/update the dependency lock and software bill of materials (SBOM); scan known vulnerabilities and license policy.
5. Review install/build scripts, network calls, telemetry, subprocesses, filesystem access, dynamic code execution, and update mechanisms.
6. Map all external tools to Yuki's permission and audit interfaces. Reject alternate approval paths and implicit credential access.
7. Add unit, security, cross-platform packaging, and offline/mock-provider tests. Include adversarial prompt-injection and tool-output tests.
8. Benchmark task success, recovery, latency, memory, package size, and provider coverage against the current implementation.
9. Keep the integration optional until it passes acceptance tests. Pin versions and document rollback/removal.
10. Record the decision, test evidence, and known limitations. Legal review is needed if license or provenance is ambiguous.

## Acceptance gates for a candidate adapter

- Runs on supported Windows and Linux CI.
- Supports local Ollama without requiring cloud credentials; cloud providers remain explicit opt-in.
- Cannot execute a tool outside Yuki's registered capability set.
- Requires approval for every consequential write/action unless a narrowly scoped, user-configured policy explicitly allows it.
- Records task start, tool proposal, approval, execution result, verification evidence, and failure in the same durable task timeline.
- Recovers interrupted work without automatically repeating non-idempotent actions.
- Has bounded prompts, outputs, retries, runtime, and resource usage.
- Passes dependency/license/security review and can be disabled without breaking the base CLI or desktop app.

## Delivery plan

1. Add this decision record and keep the core dependency footprint unchanged.
2. Prototype Microsoft Agent Framework and LangGraph against the same minimal task lifecycle; compare to the current engine.
3. Separately prototype OpenHands SDK for a sandboxed coding task.
4. Select at most one general orchestration engine and one optional coding-agent adapter based on evidence.
5. Only then propose a pinned optional dependency and integration PR. Do not merge the large integration PR solely because its mergeability flag is true.

## Upstream references

- Microsoft Agent Framework: https://github.com/microsoft/agent-framework
- LangGraph: https://github.com/langchain-ai/langgraph
- OpenHands Software Agent SDK: https://github.com/OpenHands/software-agent-sdk
- Open Interpreter: https://github.com/openinterpreter/openinterpreter
- Continue: https://github.com/continuedev/continue
- AutoGen (archived): https://github.com/microsoft/autogen
