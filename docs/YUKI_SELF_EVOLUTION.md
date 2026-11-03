# Yuki supervised self-evolution worktrees

The initial evolution-worktree utility prepares an isolated Git worktree for a
future, supervised code-improvement task. It is intentionally not an autonomous
code executor.

## Requirements

- Git installed and available on PATH
- A Git repository with a clean working tree
- Permission to create local tags, branches, and worktrees

## Start an evolution workspace

From the Yuki repository:

```bash
python scripts/yuki_evolution_worktree.py start
```

The command refuses to start if tracked, untracked, or staged changes exist in
the source checkout. On success it:

1. Records the current commit in an annotated `yuki/checkpoint/<session-id>` tag.
2. Creates a separate `yuki/evolution/<session-id>` branch.
3. Checks out that branch into a worktree under the system temporary directory.
4. Writes local recovery metadata below the repository's common Git directory.

The JSON output includes the session branch, checkpoint tag, worktree path, and
base commit. Keep the session identifier; it is needed for cleanup.

## Discard an evolution workspace

After reviewing or preserving any work, discard the isolated workspace with:

```bash
python scripts/yuki_evolution_worktree.py discard <session-id>
```

A worktree containing changes is not removed by default. The explicit
`--force` option discards uncommitted work in that isolated worktree. If the
evolution branch contains commits beyond the checkpoint, cleanup removes the
worktree but deliberately preserves the branch so committed agent work cannot
be silently destroyed:

```bash
python scripts/yuki_evolution_worktree.py discard <session-id> --force
```

Use `--force` only after reviewing the worktree and deciding its uncommitted
changes are disposable. The annotated checkpoint tag is retained after cleanup.

## Safety boundaries

- The source checkout must be clean before a session starts.
- The utility does not run generated code, tests, shell commands from an agent,
  or arbitrary task instructions.
- It does not push branches, open pull requests, merge, or deploy.
- It does not bypass branch protection or human review.
- Recovery metadata is local operational state, not a backup. Back up Git refs
  and private state separately before relying on them for disaster recovery.
- Do not store credentials, API keys, or private user data in the worktree
  metadata or commit them to Git.
- A checkpoint tag is a local Git reference. Push or mirror refs only under an
  explicitly reviewed backup policy.

This utility is the foundation for later supervised agent execution. Agent
execution, independent review, policy enforcement, and approval-gated PR
creation must be implemented and tested as separate capabilities before
enabling autonomous repository changes.
