# Personal companion and automation capability status

This document distinguishes working foundations from planned capabilities. The current source is not a claim that every integration works end to end.

## Added in this phase

- `companion/identity_manager.py`: consent-gated local identity labels only. No face templates, voiceprints, webcam capture, microphone capture or identity recognition.
- `companion/mood_engine.py`: mood adaptation based on explicit user self-report. It does not infer mood from faces, voice, or surveillance.
- `skills/identity_skill.py`: list/delete saved labels; enrollment through the skill is deliberately blocked until a dedicated consent flow exists.
- `skills/wiki_skill.py`: Wikipedia article fetch through the existing HTTPS host allow-list. Add `en.wikipedia.org` to `web_allowed_hosts` before use; web content remains untrusted.
- `skills/project_skill.py`: project plan only. Does not write files, run tests, commit, push or deploy.
- `automation/git_manager.py`: Git action plan only. Does not execute Git commands.
- `skills/content_marketer.py`: content brief scaffold only; no live research or publishing.
- `skills/seo_optimizer.py`: local heuristic checks only; no keyword volume or Search Console integration.
- `skills/monetization_advisor.py`: evidence-conscious planning checklist only; no income guarantee or campaign launch.
- New safe skills are registered, with identity labels stored under the configured workspace.

## Not implemented / not yet verified

- Global hotword listener or system tray app. Existing wake-word detector listens for a finite interval only and needs a compatible local model; no always-on microphone service is enabled.
- Outbound phone calls, telephony credentials, inbound callback handling, phone notification service, or remote control gateway.
- Camera-based face recognition, consented biometric enrollment, speaker identification, or persistent biometric profiles.
- Mood inference from voice/video; only explicit mood self-report is supported by the mood engine, and the mood engine is not yet wired into the conversational response loop.
- Real AI provider conversation/tool-call loop. The current mock provider does not generate model-driven tool calls.
- Automated project editing, test execution, Git commits/pushes, deployments or release publication from the project skill.
- Live social media scheduling/analytics, email campaign sending, ecommerce price/inventory updates, Shopify chat integration, paid ads, and multi-agent campaign execution.
- Real-time keyword research, Google Search Console, DataForSEO, ESP, social network or Shopify API integrations.
- Automatic background habit/dream cycle that survives process shutdown and restarts.

## Required safety design before enabling these capabilities

1. Use explicit per-device camera/microphone permission, visible recording state, and a hard off switch.
2. Do not enroll a friend or visitor without their informed consent. Prefer non-biometric labels unless face/voice recognition is genuinely necessary.
3. For remote phone alerts, allow-list event types and destination numbers, add rate limits, require verified telephony configuration, and never read private data aloud by default.
4. For Git and deployment, separate plan, diff, tests, commit, push and publish permissions. Require approval before each consequential boundary.
5. For marketing, require human approval before public posting, mass email, spending, customer contact, pricing changes or claims about products.
6. Keep API keys in environment secrets, never config files or source. Treat inbound messages, webpages, captions and MCP results as untrusted instructions.
7. Add integration tests with mocked providers before testing real accounts; never use production accounts for first-run tests.

## Current CI note

Check the latest GitHub Actions result before relying on this branch. A sequence of CI runs failed during this implementation, and the log endpoint was unavailable during review. These additions must not be described as tested until the latest run is green and the tests are executed on the target computer.
