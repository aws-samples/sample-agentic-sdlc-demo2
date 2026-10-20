# aidlc-platform Power

Installable Kiro Power that packages the Platform MCP server config (`mcp.json`), the persona
skills (`skills/`), and workflow steering (`dev.kiro/steering/`) for the Agentic SDLC Kiro
frontend. See `.kiro/specs/kiro-native-platform/` for the full spec.

This is the Kiro-native frontend: Kiro generates every deliverable and the MCP
server wraps a thin serverless backend (Cognito + Project API on Lambda/API Gateway + DynamoDB
+ S3 + CodeBuild) that is the system of record. There is no AgentCore Runtime/Gateway and no
runtime ARN. Each persona works a stage with the generation flow
`get_stage_brief → submit_artifact → complete_stage`, transfers ownership with
`handoff_project`, and bounces work back with `return_for_changes`; RBAC and transition
legality are enforced server-side by the Project API (403 / 422 / 409).

- The six **persona agents** and the **enforcement hooks** are shipped in the repo's `.kiro/`
  (agents are not a packageable Power component); opening the repo makes them available.
- The MCP server implementation lives in `code/kiro-mcp/` (Python; `python -m aidlc_mcp`).
- The `skills/` here **mirror** the canonical copies in `.kiro/skills/` (which the repo agents
  reference via `skill://`). Keep them in sync when editing a skill.
- `mcp.json` uses only `${VAR}` placeholders — no secrets. See the setup docs for the env vars.

Install: Kiro Powers panel -> Add Custom Power -> Import from a folder -> select this directory.
