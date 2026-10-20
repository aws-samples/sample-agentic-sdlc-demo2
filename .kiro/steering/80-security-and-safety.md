---
inclusion: always
---

# Security & Safety

## RBAC & handoff enforcement

- The Project API is the authority for handoff transitions **and** stage
  progression (`complete_stage`). It rejects illegal transitions with **HTTP
  422** and non-owning callers with **HTTP 403**, and rejects stale conditional
  writes with **HTTP 409**. Transitions are validated through
  `validate_transition` in the shared registry — keep that the only place
  transition legality is decided.
- RBAC is decided **server-side** in the Project API from the caller's verified
  Cognito group claim (owner-gate). There is no runtime component anymore — the
  Model 1 backend is the sole enforcement point. Don't loosen these checks or move
  the decision into the client.

## Auth

- Cognito: **`USER_PASSWORD_AUTH`** per-persona for the Kiro MCP server. The
  machine (client-credentials) M2M client and the `agentcore` resource-server
  scope were **removed** with AgentCore.
- Persona is the `custom:role` attribute, recorded at sign-up and mapped to a
  Cognito group by the post-confirmation Lambda trigger.
- In Model 1 the **only network-exposed endpoint is the Project API** (Cognito
  access-token authorized) plus the signature-verified Git webhook. There is no
  AgentCore WebSocket or Gateway. Any new network-exposed endpoint must be
  authenticated — flag it if it isn't.

## Secrets

- Per-project Atlassian credentials live in Secrets Manager at
  `aidlc/projects/{project_uuid}/atlassian`; the Cognito app client secret is
  also stored in Secrets Manager.
- Never read back, log, or echo secret values. Reference secrets by name/ARN.
- The Atlassian proxy resolves credentials per project (with a global ARN
  fallback) and calls Atlassian REST APIs directly.

## Input handling

- Client-supplied `project_uuid` is regex-constrained in the Project API to a
  safe character set (`[A-Za-z0-9_-]`) so it is safe to interpolate into the
  DynamoDB partition key `PROJECT#{uuid}` and can never escape the reserved
  `_platform/projects/` prefix (path traversal). Preserve such guards on any new
  client-controlled key.
- Presigned upload URLs are short-lived (≈300s) and **owner-gated**; the
  `stage-artifact-url` route additionally constrains each URL to a path the active
  stage `produces`, so a client can never write outside its stage contract or hold
  broad S3 write creds. Keep TTLs short and the `produces` allowlist enforced.

## Deploy safety

- Respect the account-mismatch guard in `cdk/bin/cdk.ts` (see
  `40-conventions-cdk.md`). Deploys, teardowns, and other live-resource changes
  are high-risk — confirm before running.
