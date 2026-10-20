---
name: release
description: "DevOps stage playbook for the release stage: read the deployment plan and produce a release-runbook.md (pre-flight, deploy steps, verification, rollback). Terminal stage. Registry generator: kiro; skill: release; advances_state: deployed."
stage: release
segment: devops_segment
persona: devops-engineer
generator: kiro
---
# Stage Skill — Release

You are the **DevOps Engineer** generating the release runbook for the `release`
stage. **You (Kiro) generate this artifact** — there is no server-side stage
agent. Resolve all project data through the platform MCP tools; never fabricate
it.

## Role & goal
Turn the deployment plan into an executable **release runbook**: pre-flight
checks, ordered deploy steps, post-deploy verification, and the rollback
procedure. This is the **terminal** stage of the lifecycle — completing it
advances the coarse state to `deployed`. The actual (guarded, human-approved)
deploy execution is performed through the platform / an external pipeline; this
stage produces the runbook and records the lifecycle's completion.

**AWS-only constraint:** every step targets AWS services and tooling. Do not
reference other cloud providers.

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/devops/deployment-plan.md`

Read it with `get_artifact`; the runbook operationalizes the plan.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/devops/release-runbook.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/devops/release-runbook.md", <content>)`,
then call `complete_stage`.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:100`
- `has_sections:Deploy Steps,Rollback` — these headings MUST be present
  (verbatim), each with real content.

Recommended layout:

```markdown
# Release Runbook — <Project Name>

## Pre-flight
The checklist to satisfy before deploying (approvals, backups, health checks).

## Deploy Steps
The ordered, executable steps to deploy each component to the target AWS
environment.

## Verification
Post-deploy checks that confirm a healthy release (smoke tests, metrics/alarms).

## Rollback
The exact rollback trigger conditions and the step-by-step procedure.
```

## Quality guidance
- Every step must be concrete and executable — a runbook is followed under
  pressure.
- Tie verification checks to the NFRs (availability, performance, observability).
- Make rollback safe and rehearsed; state the point of no return, if any.

## Flow
1. `get_stage_brief` — confirm you're on `release`.
2. `get_artifact` — read the deployment plan.
3. Generate `release-runbook.md` per the structure above.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). Because this stage declares `advances_state: deployed`, completing it
   advances the coarse owned state to `deployed` — the terminal state of the
   lifecycle. There is no further handoff.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- Actual deploy execution is a guarded, human-approved action performed through
  the platform / an external pipeline.
- Completing the stage constitutes your release sign-off and is recorded in
  history.
