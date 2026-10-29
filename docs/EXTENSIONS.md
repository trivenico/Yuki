# Installing Yuki skills and plugins

Yuki's desktop console and full CLI use the same runtime and user extension store. The desktop window includes **Install Skill**, **Install MCP Plugin**, **List Skills**, and **List Plugins** buttons; the same operations are available from the command line. The `skills install` and `plugins install` manifest formats update the active registry while the runtime is running. The separate risk-tagged `skills packs install` format is loaded on the next runtime start; its timing is documented below.

## Skills

A skill pack is a declarative JSON instruction bundle. It does **not** install Python code or grant new operating-system permissions. Copy `skill-pack.example.json`, edit the instructions, then install it:

```text
python yuki_cli.py skills install path/to/your-skill.json
python yuki_cli.py skills list
python yuki_cli.py skills remove meeting_notes
```

From the desktop/interactive runtime, use:

```text
/skills install path/to/your-skill.json
/skills
/skills remove meeting_notes
```

The skill becomes a tool in Yuki's shared skill registry. Its instructions are treated as untrusted task guidance: they cannot override Yuki's safety policy, permission checks, or tool allow-lists.

### Risk-tagged data-only skill packs

For a separate JSON format that declares an explicit risk level (`low`, `medium`, or `high`), use the nested CLI commands below. This format is stored in the `skill_packs/` user-data directory and is loaded on the next runtime start. Runtime names are prefixed with `pack_` to avoid collisions with built-in skills.

```text
python yuki_cli.py skills packs install path/to/skill-pack.json
python yuki_cli.py skills packs list
python yuki_cli.py skills packs remove release_notes
```

These packs contain instructions only, not executable code. Medium- and high-risk packs carry an additional approval requirement; all skill invocations still pass through Yuki's normal human approval gate. The `skills install` command above remains the existing declarative extension-manifest format, while `skills packs install` is the risk-tagged pack format.

## Plugins

The initial plugin format registers an explicitly configured HTTPS MCP server and a fixed list of tool names. It does not download or execute plugin code. Copy `mcp-plugin.example.json`, replace the example host with a service you trust, and choose only the tools you want Yuki to expose:

```text
python yuki_cli.py plugins install path/to/your-plugin.json
python yuki_cli.py plugins list
python yuki_cli.py plugins remove example_docs
```

From the desktop/interactive runtime:

```text
/plugins install path/to/your-plugin.json
/plugins
/plugins remove example_docs
```

The endpoint must use HTTPS, its hostname must be in `allowed_hosts`, and `allowed_tools` is an explicit allow-list. Discovering a remote tool does not grant execution permission; invoking it still requires the existing exact-confirmation path. The current manifest does not configure credentials, so authenticated services need a supported secure auth integration before they can be used.

Installed extension files live under Yuki's writable data directory: `installed_skills/` and `installed_plugins/`. They are local to that user and are not committed into the source repository.

## Learning and evolution

Yuki's existing evolution workflow records local observations and proposes workflow/habit improvements for review. For example, from the interactive runtime:

```text
/evolution record feedback | The meeting-notes skill should always separate decisions from action items.
/evolution status
/evolution run
/evolution review PROPOSAL_ID accept
```

The cycle analyzes only explicitly recorded observations; it does not call a model, execute tools, or change code/config. Acceptance records approval for manual follow-up—it does not silently apply a change. Installed skill instructions can be improved by editing and reinstalling a manifest, but Yuki does not automatically rewrite or execute its own Python code. A future self-upgrade workflow should generate a patch, run tests and security checks, show a diff, and require explicit approval before applying it.

## Import Agent Skills `SKILL.md`

Yuki can import the Markdown `SKILL.md` format as a declarative prompt skill, either by selecting the file or its containing directory.

```text
python yuki_cli.py skills import path/to/SKILL.md
python yuki_cli.py skills import path/to/skill-directory
python yuki_cli.py skills list
```

From the interactive runtime, use `/skills import path/to/SKILL.md` or `/skills import path/to/skill-directory`. The desktop UI includes **Import Skill** for a file and **Import Skill Folder** for a directory. The directory must contain `SKILL.md` at its top level. The importer validates bounded YAML frontmatter (`name` and `description`) and a bounded Markdown body, then stores it in Yuki's existing user-installed skill registry. It currently imports only that document: sibling scripts, executables, and resources are not installed or run. The resulting instructions remain untrusted guidance and do not grant permissions or bypass the approval gate. Review imported skill content before using it, especially if it came from an untrusted source.

This is a deliberately limited compatibility bridge, not full implementation of every Agent Skills ecosystem feature. Tool declarations, referenced resources, executable scripts, and framework-specific metadata are not imported automatically.
