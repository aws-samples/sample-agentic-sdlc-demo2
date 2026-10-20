---
inclusion: always
---

# Workflow Registry — Single Source of Truth

`workflow_registry.json` is the single source of truth for personas, segments,
states, and legal handoff transitions. Personas, RBAC, workflow segmentation,
session entry, and status labels all derive from it. **Do not hardcode persona
names, states, or transitions anywhere else.**

## The surviving copies

With AgentCore removed (Model 1), the runtime image copy and the React/TS mirror
are gone. The registry now exists in **two** places that must stay consistent:

1. `cdk/custom-resources/workflow_registry.json` (+ `.py` helper) — the Project
   API Lambda asset. **This is the canonical source.**
2. `code/kiro-mcp/aidlc_mcp/workflow_registry.json` — the Kiro MCP server copy
   (so the frontend can resolve stages/skills without a round-trip).

These JSON copies must be kept **consistent**. If you edit one, edit/sync the
other in the same change. (`scripts/package-code.sh` no longer syncs from a
runtime image — the CDK asset copy is the sole canonical Python copy.)

## Stage contracts (Model 1)

The registry carries a `stage_contracts` map describing how each active stage is
produced. Every active stage is `generator: kiro`. Per-stage fields:

- **`generator`** — `kiro` for all active Model 1 stages (Kiro produces the
  artifact; there is no server-side stage agent).
- **`skill`** — the `.kiro/skills/<persona>/<stage>` playbook name that guides
  generation; every reference must resolve to a file.
- **`consumes[]`** — input artifact paths (relative to the project prefix) the
  stage reads.
- **`produces[]`** — output artifacts, each `{path, required, acceptance[]}`;
  `submit_artifact` is constrained to these paths and `complete_stage` validates
  them.
- **`advances_state`** (optional) — the coarse owned `handoff_state` this stage
  advances to on completion (within the same owner); stages without it only move
  the fine-grained `current_stage` cursor.

## How to work with it

- Resolve transitions through the registry helpers — `forward(state)`,
  `request_changes(state)`, `validate_transition(current, target)` — never with
  ad-hoc state literals. The Project API derives the active stage brief from the
  registry + the project's `current_stage` cursor.
- Any registry change must keep `validate()` closure intact: every `owning_role`
  exists in `personas`, every state `segment` exists in `segments`, every
  `forward`/`request_changes` target is a defined state, the forward chain from
  `initial_state` terminates without a cycle, and (Model 1) each `stage_contract`
  is well-formed — its `skill` resolves, its stage is listed in the segment's
  `stages`, and any `advances_state` is a legal forward target for the owning
  role.

## Adding a persona (workflow plumbing only)

Add the persona (`display`, `order`), a `segment` (`owning_role`, `stages`,
`entry_node`, `exit_hub`), the new `states`, any `request_changes` edge, and a
`stage_contract` (`generator: kiro`, `skill`, `consumes`, `produces`,
`advances_state`) for each new stage — then sync both copies. The six standard
Cognito groups already exist in `cdk/lib/aidlc-platform-auth.ts`. Note: this
wires the state machine/handoff/RBAC and the stage contracts; for a new segment's
stages to produce artifacts, author the referenced Kiro Skill under
`.kiro/skills/` (Model 1 — Kiro generates; there is no orchestrator to enable).
