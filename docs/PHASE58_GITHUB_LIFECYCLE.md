# Phase 58 — GitHub lifecycle integration

## Requirements
Install Git and the official GitHub CLI (gh), then authenticate interactively using gh auth login. Yuki never reads or stores GitHub access tokens. This skill is restricted to the Yuki workspace and configured github_allowed_roots.

## Supported operations
- auth_status: check whether gh is authenticated; account details are not returned.
- create_repo: create a private or public repository with explicit visibility and publish the clean, already committed current branch from an allow-listed local repository.
- create_pr: open a pull request from the current branch to a specified base branch. The working tree must be clean; the branch must differ from the base.
- list_issues: list at most 30 issues with bounded fields.
- ci_status: list at most 10 recent GitHub Actions runs.

## Explicit confirmations
Repository creation requires CREATE owner/name private or CREATE owner/name public.
PR creation requires PR owner/name feature-branch -> main (substitute the actual repository, current branch and base).
Write actions also require Yuki's normal high-risk skill approval. Read-only actions do not change repository state.

## Safety and limitations
The CLI uses the operator's own gh authentication and OS permissions. Do not configure Yuki to run against untrusted workspaces. Repository creation publishes existing commits; it never stages or commits files. Confirm visibility and contents before use. Repository creation can succeed even if the subsequent push fails; verify remote state in that case. Branch protection and GitHub permissions still apply. No token is printed by this plugin.

Unit tests mock process interactions and do not contact GitHub. A real operation requires local gh installation, login, network access and explicit approval.
