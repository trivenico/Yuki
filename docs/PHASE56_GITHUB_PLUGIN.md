# Phase 56 — GitHub workspace plugin

## Purpose
Give Yuki a narrowly scoped GitHub publishing workflow through the existing approved-skill system, using the operator's installed Git credential helper (GitHub CLI, credential manager, or SSH agent) instead of storing or extracting credentials.

## Supported now
- Inspect repository root, current branch, GitHub origin, and working-tree cleanliness.
- Push an already committed current branch to the exact branch of its configured GitHub origin.
- Require a clean working tree and an exact second confirmation string: `PUSH owner/repository branch`.
- Use Git without a shell, with a timeout, no stdin, and sanitized subprocess failures.
- Restrict paths to configured roots. By default the allowed root is Yuki's workspace; set `YUKI_GITHUB_ALLOWED_ROOTS` to a platform path-list containing additional trusted project roots.
- Only HTTPS github.com remotes without embedded credentials or the canonical git@github.com SSH form are accepted.

## Not supported yet
This plugin does not create repositories, stage files, make commits, rewrite history, create pull requests, install GitHub Apps, or manage issues. It will not push dirty or uncommitted work. These operations should be added as separate least-privilege capabilities with their own confirmation and tests.

## Setup
1. Install Git.
2. Authenticate with GitHub using an official method such as GitHub CLI or Git Credential Manager, or configure an SSH key with the operating system's credential store/agent. Yuki does not read tokens from files.
3. Put projects under Yuki's workspace, or configure `YUKI_GITHUB_ALLOWED_ROOTS` with explicit absolute paths. Restart Yuki after changing environment configuration.
4. Ask Yuki to run the `github_push` skill with JSON input, for example:
   `{"action":"status","repo_path":"/absolute/path/to/project"}`
5. After reviewing the status and ensuring all changes are committed, request push using:
   `{"action":"push","repo_path":"/absolute/path/to/project","confirm":"PUSH owner/repository branch"}`
6. Approve the high-risk skill invocation when Yuki asks. Both skill approval and exact confirmation are required.

## Security model
Python plugins execute with Yuki's OS permissions; this plugin is not a sandbox. Keep plugin discovery disabled for untrusted code. Do not put tokens in prompts, Git remotes, repository files, or logs. Review every push and keep GitHub repository branch protection enabled.

## Validation
Unit tests mock Git and never contact GitHub. CI passing proves the plugin's guard logic only; a real push requires the operator to configure credentials and explicitly approve the action.
