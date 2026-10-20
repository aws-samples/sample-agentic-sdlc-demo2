---
inclusion: fileMatch
fileMatchPattern: "cdk/custom-resources/**/*.py|code/kiro-mcp/**/*.py"
---

# Python Conventions (Project API Lambdas + Kiro MCP server)

Applies to the surviving Python in Model 1:

- the custom-resource / **Project API** Lambdas (`cdk/custom-resources/`) — the
  system of record over DynamoDB, plus the shared workflow registry helper and
  the validators/post-confirmation trigger, and
- the **Kiro MCP server** (`code/kiro-mcp/aidlc_mcp/`) — the local (stdio)
  frontend server wrapping the Project API + S3.

Target runtime is Python 3.12+.

> The former Bedrock AgentCore runtime (`code/agentcore-runtime/`) and the Gateway
> tool Lambdas were deleted in the Model 1 migration — their SharedState /
> Strands-graph / orchestrator / HITL-WebSocket / `aidlc-state.md` conventions no
> longer apply. The `code/agentcore-gateway/lambda-tools/` code remains in-repo
> with its own tests but is **not** part of the active deployed path; don't build
> new Model 1 work against it.

## Workflow registry

- `workflow_registry.json` (+ `.py` helper) is the single source of truth for
  personas, segments, states, and transitions. The canonical copy is
  `cdk/custom-resources/workflow_registry.json`; the Kiro MCP server keeps a copy
  at `code/kiro-mcp/aidlc_mcp/workflow_registry.json`. **Keep the two consistent —
  edit both in the same change.**
- Resolve transitions through the registry helpers (`forward`, `request_changes`,
  `validate_transition`); never hardcode state literals. Preserve the `validate()`
  closure and the `stage_contract` well-formedness (skill resolves, stage listed
  in its segment, any `advances_state` is a legal forward target).

## Project API (system of record)

- Records live in DynamoDB (`AidlcProjects`, key `PROJECT#{uuid}` / `META`), with
  append-only `HISTORY#{ts}` entries — never rewrite the whole record to log a
  transition.
- Concurrency uses conditional writes (stale write → HTTP 409). RBAC is
  owner-gated server-side from the verified Cognito group claim (403 non-owner /
  422 illegal transition). This is the sole enforcement point — don't move the
  decision elsewhere.
- Acceptance validation in `complete_stage` is lenient (v1): rule failures are
  warnings; a missing *required* artifact is the one hard block.

## Kiro MCP server

- Tools map to Project API calls + S3; the Model 1 generation flow is
  `get_stage_brief` / `submit_artifact` / `complete_stage`, with `handoff_project`
  and `return_for_changes`. Don't reintroduce removed tools (`run_stage`,
  `answer_prompt`, `advance_project`, `stage_status`, `close_stage`).
- The artifact prefix is **stored** as `aidlc-docs/`; the `agentic-sdlc` label is
  display-only (`_display_key` in `tools.py`) and must never be used as a real S3
  key.

## Secrets & input handling

- Resolve Atlassian credentials per project from Secrets Manager
  (`aidlc/projects/{project_uuid}/atlassian`), falling back to the global env ARN.
  Never read back, log, or echo secret values — reference by name/ARN.
- Validate external input: the Project API constrains `project_uuid` with a regex
  (`[A-Za-z0-9_-]`) to keep the DynamoDB partition key and the reserved
  `_platform/projects/` prefix safe from traversal. Keep such guards on any new
  client-controlled key.
- Presigned upload URLs are short-lived and constrained to a path the active stage
  `produces` — keep TTLs short and the allowlist enforced.

## Style

- Match the existing module docstring + pure-helper style (see
  `workflow_registry.py`). Prefer pure functions over hidden global state.
- Use parameterized AWS SDK calls; validate external input.
