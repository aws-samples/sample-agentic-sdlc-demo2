# Architecture documentation set

Engineering-level reference for the Kiro-native platform: Kiro IDE is the
only frontend and generates every deliverable; a thin serverless backend (the
Project API on DynamoDB + S3 + Cognito + CodeBuild) is the authoritative system of
record. There is no AgentCore and no web UI.

Start with the primary document, then drill into the data model and flows:

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the authoritative architecture:
  system context and component diagram, frontend/backend layers, the generation flow,
  the workflow registry and handoff state machine, the Git round-trip, security model,
  and stable identifiers.
- [`data-model.md`](data-model.md) — the DynamoDB single-table design: the `META`
  business record and append-only `HIST#` items, the GSI access-pattern table, and
  optimistic concurrency (conditional writes / `TransactWriteItems` → HTTP 409).
- [`sequence-flows.md`](sequence-flows.md) — Mermaid sequence diagrams for creating a
  project and generating a stage, cross-persona handoff and reviewer bounce, the Git
  round-trip, and the QA test-runner.

Related docs: [`../deployment-steps.md`](../deployment-steps.md),
[`../../code/kiro-mcp/docs/setup.md`](../../code/kiro-mcp/docs/setup.md), and the
[`kiro-native-platform`](../../.kiro/specs/kiro-native-platform/) spec.
