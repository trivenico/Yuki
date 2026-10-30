# Third-party notices

## OpenJarvis benchmark statistics

- Project: OpenJarvis — https://github.com/open-jarvis/OpenJarvis
- Source file: `src/openjarvis/bench/_stats.py`
- Source revision: `336a68ac3b6206e3f3037c5c6d9d40722c4ab4cb`
- Reused in Yuki: `providers/evaluation_stats.py`
- Copyright: Copyright 2025 The OpenJarvis Authors.
- License: Apache License 2.0.
- Changes: The upstream statistics helper is copied with its implementation preserved and an attribution header added. Yuki wires it into `providers/model_evaluation.py` to expose latency distribution metrics. The full license text is in `licenses/Apache-2.0-OpenJarvis.txt`.

OpenClaw and Hermes Agent were reviewed as upstream format and architecture references for the Agent Skills Markdown importer. No executable source files or bundled skill text from those projects are copied in this change. Their repository licenses remain MIT; any future copied files will receive file-level attribution and retained license notices.
