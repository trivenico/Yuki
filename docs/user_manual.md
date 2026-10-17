# Yuki User Manual

## 1. What Yuki is

Yuki is a modular Python personal-agent project with a guarded skill registry, optional local AI provider, local notes, and opt-in integrations. Read the root README for the current capability audit. A module existing in the repository does not mean its integration is production-ready.

## 2. Requirements and setup

1. Install Python 3.11 or newer.
2. Clone the repository and create a virtual environment.
3. Install dependencies with `python -m pip install -r requirements.txt`.
4. Review `config.yaml`. Safe defaults select the mock provider and leave voice, Gmail, plugin discovery, security lab, and firewall features disabled.
5. Run `python -m unittest discover -s tests -v`.
6. Start the terminal app with `python main.py`.

## 3. Common commands

Type `/help` in the running application. The command list includes status, skills, AI questions, bounded tasks, explicit skill requests, planning, local memory, reminders, voice status, and security-lab status. Consequential actions may require fresh approval.

## 4. AI providers

The mock provider is a safe default. Ollama requires a separately installed and running Ollama service and a configured model. Cloud routing is opt-in and may transmit data outside your device; inspect provider configuration and privacy needs before enabling it. Never put API keys in YAML, source code, logs, or issue reports.

## 5. Memory and workspace

Review the configured workspace before storing private notes. Back up data before upgrades. Do not manually edit state files while Yuki is running. Test restore procedures before relying on local memory.

## 6. Plugins and skills

Python plugin imports execute with Yuki's process permissions; plugin loading is not a sandbox. Keep discovery disabled unless you reviewed each trusted module. Never run unknown plugins or downloaded code against credentials or sensitive files.

## 7. Voice

Voice support depends on optional packages, model files, microphone permissions, and platform audio support. Check `/voice status`. A wake-word model path does not by itself prove continuous listening is active. Do not enable always-on capture without informed consent and clear indicators.

## 8. Security features

Security-lab, browser, Gmail, MCP, Docker, and firewall features have separate dependencies and opt-in controls. Only scan systems you own or are explicitly authorized to assess. Dry-run firewall mode should remain enabled until reviewed. Prompt-injection scanning is heuristic and is not a security boundary.

## 9. Troubleshooting

- Missing dependency: install only reviewed requirements in the active virtual environment.
- Ollama unavailable: verify service status, URL, model name, and client timeout.
- Skill denied: read the approval request and approve only if the exact action is intended.
- Tests fail: preserve the traceback, run the smallest failing test, and do not suppress it to make CI green.
- Corrupt or unreadable state: back up the original file before attempting recovery; never delete memory databases merely because a check fails.

## 10. Privacy, telemetry, and identity

Telemetry should collect operational metadata only and must not record raw prompts, completions, credentials, or tool arguments by default. Identity private keys must remain outside Git and be protected by operating-system permissions or a secret manager. A signature is trusted only when its public-key fingerprint is enrolled in a trusted registry.

## 11. Production-readiness status

Yuki is under active development. Open the production-readiness roadmap and capability audits before deploying. Do not expose the terminal app or optional services to an untrusted network without a separate threat model and security review.
