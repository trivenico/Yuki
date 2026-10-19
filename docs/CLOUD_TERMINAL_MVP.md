# Cloud VM terminal MVP deployment

This is the shortest supported path for a single-user terminal MVP on a cloud VM. It is not a public multi-user service. Keep the VM private behind SSH; do not expose Yuki or Ollama ports to the internet.

## Deployment shape
- Linux VM accessed through SSH.
- Python 3.11+ virtual environment.
- Yuki runs interactively in tmux; approval prompts require a real terminal.
- Ollama runs on the same VM and binds to loopback.
- Optional cloud model is environment-configured and only used for explicitly allow-listed task names. The /ask-cloud TASK | PROMPT command asks for per-request confirmation.
- config.yaml defaults to mock provider, plugin discovery off, voice off, and privileged integrations off.

## 1. Provision and connect
Create a non-root login user, use SSH keys, enable OS security updates, and restrict inbound firewall rules to SSH from your own IP. Never expose TCP 11434 publicly.

```sh
ssh your-user@your-vm
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip tmux
git clone https://github.com/trivenico/Yuki.git
cd Yuki
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Use a dedicated VM/workspace. Do not mount your home directory, SSH agent, Docker socket, or production credentials into agent workloads.

## 2. Run deterministic tests before first launch

```sh
python -m unittest discover -s tests -v
```

Do not continue to a production-like deployment if this command fails. The repository has an outstanding approval-flow integration failure and PR #10 is still a draft. This guide is a reproducible setup path, not evidence that the full suite passes. Resolve failing tests and get a green GitHub Actions run before connecting personal accounts or privileged tools.

## 3. Start with mock mode

```sh
python scripts/cloud_mvp_preflight.py
python main.py
```

Mock mode tests command flow; it is not useful model reasoning. Use /help and /status. Keep plugin discovery and privileged integrations disabled.

For a persistent terminal session:

```sh
tmux new -s yuki
. .venv/bin/activate
python main.py
```

Detach with Ctrl+B then D; reconnect with tmux attach -t yuki. Do not use a non-interactive service manager for the current CLI because approvals require an interactive terminal.

## 4. Enable local Ollama

Install Ollama using its official instructions for your VM. Keep its listener bound to loopback and download a model compatible with the VM. Set these in your current shell or a secret manager:

```sh
export YUKI_PROVIDER=ollama
export YUKI_OLLAMA_MODEL=your-installed-model
python scripts/cloud_mvp_preflight.py
python main.py
```

The model name must match an installed Ollama model. Choose a small quantized model if RAM/CPU/GPU are limited.

## 5. Explicit cloud tasks only

```sh
export YUKI_PROVIDER=hybrid
export YUKI_OLLAMA_MODEL=your-installed-local-model
export YUKI_CLOUD_MODEL=your-cloud-model-name
export YUKI_CLOUD_TASKS=research,writing
export YUKI_CLOUD_API_KEY_ENV=OPENAI_API_KEY
read -rsp "Cloud API key: " OPENAI_API_KEY; export OPENAI_API_KEY; echo
python scripts/cloud_mvp_preflight.py
python main.py
unset OPENAI_API_KEY
```

Use a cloud secret manager for longer-lived deployments. Never paste the key into chat, commit it, put it in config.yaml, or include it in a command-line argument. The general provider route remains local; cloud use requires a named allow-listed task and per-request terminal confirmation. Do not send private memory, credentials, health records, or other sensitive data to a cloud provider.

The preflight reports only whether a key is present; it never prints the value and does not send a model request.

## 6. Operational controls
- Restrict SSH and keep OS security updates enabled.
- Use encrypted disks and encrypted off-VM backups; test restore.
- Keep plugins, voice capture, email integrations, security lab, firewall changes, and device controls off until individually reviewed.
- Keep the terminal session interactive so protected actions can be reviewed.
- Do not run Yuki as root or enable arbitrary shell execution.
- Monitor VM costs; local inference can consume significant RAM, CPU, GPU, and disk.
- Keep a rollback path and back up private workspace data before upgrades.

## MVP acceptance gate
- [ ] Full unit suite passes locally and in GitHub Actions.
- [ ] Preflight passes in the chosen provider mode.
- [ ] Local model is installed and reachable.
- [ ] Cloud prompts require task allow-list and per-request confirmation.
- [ ] No secrets are present in Git, config files, logs, or unencrypted backups.
- [ ] Workspace backup and restore are tested.
- [ ] No public port exposes Yuki or Ollama.
- [ ] Security review approves every enabled integration.

Until all required checks pass, treat this as a private development MVP, not a production-ready autonomous companion.