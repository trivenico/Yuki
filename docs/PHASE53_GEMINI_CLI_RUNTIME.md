# Phase 53 — Safe Gemini CLI runtime integration

## Scope
Integrates the official Gemini CLI as an optional provider without browser-session scraping or API-key extraction.

## Configuration
Install and authenticate Google's official Gemini CLI in the same OS account that runs Yuki. Select provider `gemini` explicitly. `is_available()` checks that the executable exists; it cannot prove that the account is authenticated or that quota is available. The first generation call may therefore fail with a sanitized error.

## Runtime safeguards
- Only the executable named `gemini` is accepted; the executable is resolved through PATH.
- Prompt and output lengths are bounded.
- stdout/stderr are written to temporary files rather than captured into unbounded in-memory pipes; only a bounded stdout prefix is read.
- The subprocess has a deadline; timeout kills the process group on POSIX and kills the child on other platforms.
- No shell is used; stdin is closed; errors do not expose raw stderr.
- Invocation uses non-interactive prompt mode with sandbox and plan approval options. CLI flag compatibility must be confirmed against the installed official CLI version.

## Privacy
The provider sends the supplied prompt to Google's CLI service according to the signed-in account's policy and quota. Do not route confidential prompts unless authorized. Yuki does not read browser cookies, scrape sessions, or claim that a consumer login is a generic API.

## Verification limits
Tests are mocked and do not contact Google. A real CLI smoke test requires the operator to install/authenticate the official CLI and explicitly opt in. Passing unit tests does not establish production readiness.
