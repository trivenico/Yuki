# Run Yuki on an Oracle Cloud Linux VPS (CLI-first)

Status: practical first-run guide for the current restricted developer preview.
This guide does not claim Yuki is production-ready or an autonomous agent.

## Scope and expectations

Start with one Linux VPS, one owner account, one provider, and the terminal.
Do not expose Yuki's development interfaces to the public internet. Use SSH for
administration. The current persistent CLI chat deliberately passes an empty
tool list: it can converse and persist turns, but it cannot autonomously run
shell commands or operate the host.

A small VPS can host the Python application and SQLite-backed local state.
Cloud-model inference happens at the provider, while local-model inference
requires enough RAM/CPU (or a separately configured GPU). Start without voice,
desktop, PostgreSQL, or background agent workers.

## 1. Choose an Always Free VPS shape and prepare it

Recommended starting configuration when available in your tenancy's home region:

- Shape: `VM.Standard.A1.Flex` (Ampere ARM64).
- Allocation: Oracle's current standard Always Free documentation lists 2 OCPUs
  and 12 GB RAM total across A1 instances in the tenancy. A single 2 OCPU / 12 GB
  VM is the simplest Yuki host. Do not assume older 4 OCPU / 24 GB guides apply;
  use a larger allocation only if the OCI Console confirms it is Always Free for
  this specific tenancy.
- Image: **Ubuntu 24.04 LTS ARM64** or **Oracle Linux 9 ARM64**. Both are suitable
  for Yuki. Prefer Ubuntu 24.04 LTS for the broadest community/package guidance;
  choose Oracle Linux 9 for an Oracle-native, RHEL-family administration model.
  Select a standard server image marked Always Free Eligible, not an x86 image.
- Storage: Oracle documents 200 GB total Always Free Block Volume capacity in
  the home region, shared by boot and block volumes combined. The default boot
  volume consumes part of this allowance. Confirm the exact boot-volume size and
  estimated cost in the OCI Console; keep a separate encrypted backup outside
  the instance.

The compute and storage allowances are shared across the tenancy and depend on
account eligibility and regional capacity. Verify the shape, image eligibility,
boot-volume size, and estimated cost in the OCI Console before creating the VM.
If A1 capacity is unavailable, wait rather than silently selecting a paid shape.
The AMD micro instance has only 1 GB RAM and is suitable only for a lightweight
CLI experiment, not the preferred Yuki host. Avoid assuming that a local large
language model will fit or run well on the free VM. Start with one cloud
provider; evaluate local inference separately after measuring resources.

Create the instance with SSH access, a non-root sudo-capable operator account,
and enough free disk for the checkout, virtual environment, logs, and data.
Keep inbound network rules limited to SSH from your trusted IP/network. The
CLI does not need an inbound application port.

Connect over SSH and inspect the operating system and resources:

```sh
cat /etc/os-release
uname -m
free -h
df -h
```

Install Git and Python tooling using the package manager for your image.

Ubuntu 24.04 LTS:

```sh
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

Oracle Linux 9:

```sh
sudo dnf install -y git python3 python3-pip
```

Yuki requires Python 3.10 or newer. Check before creating the environment:

```sh
python3 --version
```

If the distribution's `python3` is older than 3.10, install a supported
Python 3.10+ package available for that Oracle Linux release and use its
explicit executable (for example `python3.11`) in the commands below. Do not
replace the operating system's system Python.

## 2. Get the source and create an isolated environment

Clone the repository and create a dedicated virtual environment:

```sh
git clone https://github.com/trivenico/Yuki.git
cd Yuki
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

If you installed Python under a versioned executable name, substitute that
name for `python3` when creating the virtual environment. The editable install
provides the `yuki` CLI entry point and installs the core project dependency.
Do not use `sudo pip` and do not install project packages into system Python.

## 3. Put mutable data outside the Git checkout

Use a dedicated owner-only data directory. This keeps conversation state and
workspace files out of the source tree:

```sh
mkdir -p "$HOME/.local/share/yuki"
chmod 700 "$HOME/.local/share/yuki"
export YUKI_HOME="$HOME/.local/share/yuki"
```

Add the `YUKI_HOME` export to the operator's shell profile (for Bash, usually
`~/.bashrc`), then start a new login shell or source that file. Yuki's first
provider setup writes non-secret preferences to the per-user config directory
(`~/.config/yuki/config.yaml` on Linux) with owner-only permissions. Keep
provider credentials in environment variables or a secret manager, never in
YAML or Git.

The configured workspace defaults to `./yuki_workspace`, resolved beneath
`YUKI_HOME`. Keep backups of this data directory separate from the VPS.

## 4. Run diagnostics and configure one provider

With the virtual environment active:

```sh
yuki doctor
yuki setup
yuki provider-status
```

Choose one provider during setup:

- **OpenAI:** setup records the selected model but never asks for the API key.
  Export `OPENAI_API_KEY` in the shell or inject it from a secret manager.
- **Ollama:** install and start Ollama separately, pull a model supported by
  the available VPS resources, and select its exact installed model name.
  Keep Ollama bound to loopback; do not expose its port publicly.
- **Gemini CLI:** install and authenticate the Gemini CLI separately for the
  same Linux user that runs Yuki.

Provider status is not always a live connectivity test. When you are ready to
send a real, fixed probe request to the selected provider, run:

```sh
yuki provider-test
```

This makes an external provider request for the configured cloud provider.
Review its privacy, billing, and data-handling terms first.

## 5. Start Yuki

Try a single prompt:

```sh
yuki ask "Introduce yourself and explain your current capabilities."
```

Then start persistent terminal chat:

```sh
yuki chat
```

Type `/help` for the available chat commands and `/quit` to exit. Conversation
history is stored in the configured local workspace. The current CLI chat
explicitly disables tool execution; it does not yet autonomously run jobs,
modify its own source, or deploy upgrades.

## 6. Safe operating routine

- Start Yuki as the unprivileged owner account, never as root.
- Keep SSH access restricted; do not open a public port for the CLI.
- Keep plugin discovery, voice, security lab, firewall automation, and sandbox
  features disabled until each has a reviewed deployment and acceptance test.
- Do not add `*` to `trusted_plugin_modules`: trusted Python plugins execute
  with Yuki's operating-system permissions.
- Back up `$YUKI_HOME` and the per-user config directory, encrypt backups, and
  periodically test restoration.
- Update from reviewed Git commits. Inspect changes, install into the virtual
  environment, run diagnostics and tests, and retain a known-good version.
- Do not allow Yuki to rewrite its live installation or run generated code on
  the host. Self-improvement must first produce an isolated candidate, run
  reproducible checks, and wait for owner approval before activation.

## 7. Current limitations and next acceptance gates

This is a CLI-first developer preview, not a managed daemon or unattended
production service. Keep the first deployment in an interactive SSH session.
A later operations milestone should add a documented service manager unit,
structured logs, health checks, graceful restart, disk/resource limits,
backup/restore automation, and tested upgrade/rollback procedures.

Before relying on the VPS for paid or confidential work, verify on that exact
instance: fresh install, provider connection, persistent conversation across
restart, file permissions, backup restoration, resource usage under realistic
load, and all required GitHub Actions checks. Do not infer successful operation
from a successful package installation alone.
