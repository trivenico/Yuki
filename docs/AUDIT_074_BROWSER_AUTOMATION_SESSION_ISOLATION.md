# Audit 074 — Browser Automation Security, Session Isolation & Browser-Agent Safety

**Scope:** Playwright lifecycle, navigation and request policy, cookies/storage, downloads, page scripts, browser skill exposure, and execution isolation.

## Reviewed source
- automation/browser.py
- skills/browser_skill.py
- requirements-browser.txt
- core/guarded_skill_runtime.py
- core/sandbox_runtime.py
- Repository tree for browser/session/download-related modules and tests

## Positive controls observed
- SafeBrowser.read_page creates a new Playwright browser process and a new browser context per invocation; it does not intentionally reuse a persistent profile or context across calls.
- Context downloads are disabled and service workers are blocked.
- Browser skill is explicitly read-only, marked medium risk, and requires one-shot human approval.
- Browser output is labelled untrusted and scanned by the prompt guard before returning to the agent.
- The initial URL requires HTTPS, exact hostname allow-list membership, default HTTPS port, no URL credentials, and public DNS answers.
- Browser requests are intercepted; redirects and subresources are routed through the request guard.
- The browser is closed after successful reads; companion fix now ensures cleanup on exceptional paths as well.

## Findings

### P1 — Routed browser requests did not enforce the complete URL policy (fixed in companion PR)
The initial navigation validator rejected non-default ports and URL userinfo. However, the Playwright route handler only checked HTTPS plus allow-listed hostname. A redirect, iframe, or subresource could therefore request the same allow-listed host on a non-default port, or use a URL with userinfo, despite the initial navigation rejecting those forms.

**Fix submitted:** centralize strict URL parsing and apply the same HTTPS/default-port/no-userinfo/exact-host policy to every routed browser request and the final navigation URL. Malformed ports and malformed DNS results are normalized to safe browser errors. Context/browser cleanup is guaranteed through finally. Regression tests cover allowed URL, nonstandard port, userinfo, HTTP/unlisted hosts, malformed ports, and mixed public/private DNS answers.

### P1 — Chromium socket destination is not DNS-pinned by Playwright route validation (residual risk)
The initial URL is DNS-checked before Chromium navigates, but Chromium resolves the hostname independently when it opens the connection. The route handler validates URL syntax and host policy, not the actual IP address selected for Chromium's socket. Therefore this layer does not prove that the browser connects to one of the previously validated public IPs. DNS rebinding against an allow-listed hostname remains a residual risk.

**Recommendation:** run browser traffic through a trusted egress proxy that resolves, validates, and pins destinations for every connection, or use a network namespace/container policy that blocks private, loopback, link-local, and metadata ranges. The proxy must preserve TLS verification and reject redirects/re-resolutions to blocked destinations. Do not claim that a Playwright URL route alone is an SSRF boundary.

### P2 — Page JavaScript executes in the browser context
The reader does not click or submit forms through its public skill interface, but it does load pages with JavaScript enabled and extracts rendered body text. Service workers are blocked and downloads are disabled. JavaScript execution is part of normal page rendering, so the browser process remains exposed to the browser engine's attack surface and hostile page behavior.

**Recommendation:** run Chromium in an isolated, unprivileged worker/container with no host credentials, no mounted user profile, strict resource limits, patched browser binaries, and network egress proxying. Consider disabling JavaScript for static-page reading or making script execution an explicit capability.

### P2 — No cross-invocation cookie/profile reuse was found in the inspected implementation
Each invocation launches a fresh browser and creates a fresh non-persistent context. This is a useful isolation property, but the reviewed source does not establish a separate OS/container security boundary for Chromium. It also does not establish behavior for future persistent-context or authenticated-browser features.

**Recommendation:** preserve fresh-context defaults. If authenticated sessions are introduced, isolate them by user and tenant, encrypt storage state, restrict export, bind approval to the exact session and destination, and test cross-user cookie/storage isolation.

## Remediation status
- **Implemented in companion PR #1135:** apply full strict URL policy to every Playwright-routed request; harden parsing and DNS errors; guarantee cleanup; add regression tests.
- No browser DNS pinning claim is made. It requires a network-layer proxy or sandbox egress control.
- No authenticated session or persistent profile feature was identified in the reviewed SafeBrowser path; no speculative session-store redesign was added.

## Validation status
- Source inspection completed for the listed browser/skill/runtime paths.
- Regression tests added but **not executed**.
- No Playwright browser binary, exploit page, DNS rebinding harness, container isolation, proxy, or packet-level test was run.
- PR remains open and unmerged; no deployment claim is made.