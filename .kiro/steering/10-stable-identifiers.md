---
inclusion: always
---

# Stable Functional Identifiers — Do Not Rename

These identifiers are the stable interface between the Project API and the Kiro
MCP frontend. Downstream artifacts and tools depend on them. Leave them verbatim
when refactoring, renaming, or rebranding. Changing one is a breaking change that
must be coordinated across both layers.

| Identifier | Where it lives |
| --- | --- |
| Stack name `AIDLCPlatformWorkshop` | `cdk/bin/cdk.ts` |
| Bucket prefix `aidlc-assets-…` | `scripts/package-assets.sh` |
| Artifact prefix `aidlc-docs/` | Project API, agent Skills, Kiro MCP server |
| Project Store prefix `_platform/projects/` | `cdk/custom-resources/project_api.py` (reserved) |
| Secrets path `aidlc/projects/{id}` | per-project Atlassian + Git credentials |

> **Record store note (Model 1):** project records now live in **DynamoDB**
> (`AidlcProjects`, key `PROJECT#{uuid}` / `META`), not in S3-JSON. The
> `_platform/projects/` object-store prefix is **retained but unused** — it stays
> reserved so a client-supplied `project_uuid` can never collide with a
> `{slug}/` artifact tree (path-traversal guard in `project_api.py`). Keep the
> prefix and the `project_uuid` character-set guard verbatim.

> **Display vs. storage:** the artifact prefix is **stored** as `aidlc-docs/`
> (the stable key — keep it verbatim everywhere). The Kiro MCP server may
> **display** it as `agentic-sdlc` via a label-only remap (`_display_key` in
> `code/kiro-mcp/aidlc_mcp/tools.py`) — the relabel never changes the S3 key,
> so do not "fix" code/docs to say `agentic-sdlc`; the real prefix is still
> `aidlc-docs/`.

Other preserved internal names that should not be casually renamed:

- Cognito group names (the persona roles): `requirements-analyst`,
  `system-architect`, `software-developer`, `qa-engineer`, `devops-engineer`,
  `maintenance-specialist`.

**Removed with AgentCore (Model 1 migration — no longer stable identifiers):**

- `icode-agent` — the agent container image / directory. The image build and
  `code/agentcore-runtime/` were deleted (kiro-native-platform task 11).
- State file `aidlc-state.md` — lived in the deleted runtime
  (`code/agentcore-runtime/icode-agent/tools/state.py`); there is no server-side
  serializer, so the token contract is gone.
- The MCP gateway tool-name scheme `file-operations___<tool>` /
  `atlassian___<tool>` — translated in the deleted runtime `tools/gateway.py`;
  the AgentCore Gateway is removed.
- The `Kiro*RuntimeArn` stack output — the Runtime is gone.

Two project identifiers are intentionally distinct — keep them separate:

- `project_slug` — slugified name, used as the S3 artifact prefix.
- `project_uuid` — the client-generated UUID (minted by the Kiro MCP server),
  used for credential lookup in Secrets Manager and as the DynamoDB record
  partition key `PROJECT#{uuid}`.
