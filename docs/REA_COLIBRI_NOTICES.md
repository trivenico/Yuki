## REA

- Project: REA — https://github.com/morluto/rea
- Reused content: workflow concepts only; Yuki's `skills/upstream/rea/rea-investigation/SKILL.md` is an independently authored integration guide, not copied REA source code.
- Upstream license: MIT for REA-authored code. Third-party tools and assets retain their own licenses.
- Integration boundary: external local MCP server; native stdio MCP launching is not implemented in Yuki yet.

## Colibri

- Project: Colibri — https://github.com/JustVugg/colibri
- Reused content: API compatibility only; `providers/colibri.py` is an independently authored client adapter and does not include Colibri source files.
- Upstream license: Apache License 2.0. Full license text is available at `https://www.apache.org/licenses/LICENSE-2.0`; Colibri's repository includes its LICENSE and NOTICE files.
- Model weights and third-party dependencies are separately licensed and are not bundled by Yuki.
