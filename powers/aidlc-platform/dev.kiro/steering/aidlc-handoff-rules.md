# Agentic SDLC — Handoff & Enforcement Rules

## Forward vs. return-for-changes

- **Forward** (`complete_stage`, `handoff_project`): progresses the project along the chain.
  `complete_stage` validates the current stage's artifacts and advances the stage cursor (and, where
  the stage declares it, the owned lifecycle state); `handoff_project` transfers ownership to the
  next persona. The Project API validates legality and ownership and resolves the single legal
  forward step.
- **Return for changes** (`return_for_changes`): sends the project back to the previous persona
  (SA->BA, Dev->SA, QA->Dev, DevOps->QA). **Always provide clear, specific comments** — they are
  recorded and shown to the receiving persona (via `get_project` -> `latest_change_request`).
  This action is always available and is never blocked by any gate.

## Enforcement gates (client-side, on top of server RBAC)

- **Review before approve:** for states where you approve upstream work (`assigned_to_sa`, `in_qa`,
  `in_deployment`), read the project's artifacts (`list_artifacts`/`get_artifact`) before calling
  `complete_stage`/`handoff_project`. Skipping review blocks the forward call; read, then retry.
- **Tests before submit:** the Software Developer's `submit_implementation` is blocked unless the
  project's tests pass locally. Run and fix tests first.
- These gates never affect `return_for_changes` — sending work back is always permitted.

## Non-negotiables

- The Project API + workflow registry are the sole authority for transition legality (422) and
  ownership (403). The Kiro layer only guides and adds restrictions; it never overrides the server.
- Secrets (git tokens, credentials) are passed through and stored securely by the server — never
  echo, log, or repeat a token.
- Artifact keys use the stored `aidlc-docs/` prefix; the `agentic-sdlc` label is display-only and
  must not be used as a real key.
