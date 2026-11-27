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

## 1. Prepare the confirmed OCI VPS

Target configuration confirmed by the owner in the OCI Console:

- Shape: `VM.Standard.A1.Flex` (Ampere ARM64).
- Compute: **4 OCPUs and 24 GB RAM**. Size the Yuki application for this
  allocation; do not downgrade the plan to the standard 2 OCPU / 12 GB
  Always Free allowance when the owner's console explicitly offers 4 / 24.
- Image: **Ubuntu Server 24.04 LTS ARM64** (standard server image, not x86).
  Use `apt` for system packages and an isolated Python virtual environment.
- Storage: **200 GB total configured storage**. Treat this as the instance's
  total boot-plus-attached storage budget unless the OCI Console explicitly
  shows separate entitlements. Record the boot volume size and any block-volume
  attachments before provisioning; keep encrypted backups outside the VPS.

The owner's console is the source of truth for this tenancy's actual shape and
billing eligibility. Before creating or resizing the VM, confirm that the
selected shape and storage are marked Always Free / covered by the intended
credit or subscription, and review the estimated cost. Do not silently choose
paid resources. A1 is ARM64, so use ARM64-compatible images and Python wheels.
Although 24 GB RAM provides room for local experiments, start with a cloud
provider and measure CPU, memory, disk, and latency before attempting local
model inference. A 4-OCPU CPU-only VPS is not a substitute for a dedicated
GPU.

Create the instance with the confirmed 4 OCPU / 24 GB A1 shape and Ubuntu
24.04 LTS ARM64 image. Use SSH access and a non-root sudo-capable operator
account. Keep inbound network rules limited to SSH from your trusted IP/network;
the CLI does not need an inbound application port. For the 200 GB disk budget,
avoid filling the boot volume with datasets or model weights: keep the OS and
application on the boot volume, put larger Yuki workspaces/datasets on a
separately mounted block volume if one is attached, and retain free space for
updates, logs, SQLite maintenance, and recovery. Verify mount points with
`lsblk -f` and `df -h` before placing data.

Connect over SSH and inspect the operating system and resources:

```sh
cat /etc/os-release
uname -m
free -h
df -h
```

Install Git and Python tooling on Ubuntu 24.04 LTS:

```sh
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

Yuki requires Python 3.10 or newer. Confirm the Ubuntu-provided version:

```sh
python3 --version
```

Use the distribution's supported Python package; do not replace the operating
system's system Python.

### Secure the initial SSH access

Before installing Yuki, confirm the OCI VCN security list or Network Security
Group allows TCP 22 only from your current trusted public IP/CIDR. Do not add
public ingress rules for Yuki, Ollama, databases, or development dashboards.
Use the SSH public key configured at instance creation; do not paste or upload
the private key to GitHub, Yuki, or chat.

After connecting, verify the operator identity and sudo access:

```sh
whoami
id
sudo -v
```

On Ubuntu, UFW can provide an additional host firewall layer. First inspect
the existing rules and ensure SSH is explicitly allowed before enabling it;
otherwise you can lock yourself out. Keep the current SSH session open and
test a second SSH login before closing it:

```sh
sudo ufw status verbose
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status numbered
```

If your SSH port is not 22, replace the OpenSSH application rule with the
actual configured port before enabling UFW. Apply OCI network rules and host
firewall rules deliberately; both must permit your trusted SSH source. Do not
disable password authentication or edit sshd configuration until key-based
login has been tested in a second session and you have a recovery path through
the OCI console.

## 2. Get the source and create an isolated environment

Clone the repository and create a dedicated virtual environment:

```sh
git clone https://github.com/trivenico/Yuki.git
cd Yuki
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip check
```

If you installed Python under a versioned executable name, substitute that
name for `python3` when creating the virtual environment. The editable install
provides the `yuki` CLI entry point and installs the declared core runtime
dependency (PyYAML); the basic OpenAI-compatible adapter uses Python's standard
library HTTP client. Do not install optional voice, desktop, or PostgreSQL
extras during the first CLI bring-up. Do not use `sudo pip` and do not install
project packages into system Python.

Verify the installed entry point and imports before configuring a provider:

```sh
yuki --help
python -c "import yaml, yuki_cli; print('Yuki CLI imports OK; PyYAML', yaml.__version__)"
yuki doctor
```

The doctor report is a local capability inventory, not a live provider test.
Keep its output for the deployment notes; it should identify Linux and
`aarch64`/ARM64 on this VPS.

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
