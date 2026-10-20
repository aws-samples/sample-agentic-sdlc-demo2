# Multi-persona demonstration (Kiro frontend)

Shows several personas collaborating on one project, live, from Kiro. All state is server-side, so
a handoff in one window/persona is visible to the next.

## Setup

1. Complete `setup.md` and export the deployment vars + the six Test_User credential pairs.
2. Choose a presentation mode:
   - **Multiple windows (recommended):** open one Kiro window per persona; select a different
     persona agent in each. Each window acts as that Cognito identity.
   - **Single window:** switch the active persona agent via the agent picker (or its
     `keyboardShortcut`) between steps — no session restart needed.

## Walk-through (forward chain + a request-changes bounce)

1. **BA — create & assign.** As **Business Analyst**: "Show me my projects" (`list_projects`),
   `create_project`, `upload_requirements` (optional). Generate requirements/stories in Kiro
   (`get_stage_brief` -> `submit_artifact` -> `complete_stage`), review with `get_artifact`, then
   "hand off to the Solution Architect" (`handoff_project` -> `assigned_to_sa`).
2. **SA — review & bounce.** As **Solution Architect**: "Show me my projects" — the project now
   shows `mine`. Read `requirements.md`/`stories.md` (`get_artifact`). Then demonstrate the
   feedback loop: "Return this to the BA — the stories don't cover the audit requirement"
   (`return_for_changes`). This is never gated.
3. **BA — see the feedback.** Back in the BA window: `get_project` surfaces
   `latest_change_request` ("Returned by Solution Architect: ..."). Address it, then
   `handoff_project` again.
4. **SA — review-gate + advance.** Try to advance *without* reading artifacts first to show the
   **review-before-approve** hook blocking `complete_stage`/`handoff_project`; then read the
   artifacts and retry successfully. Generate the design across its stages
   (`get_stage_brief` -> `submit_artifact` -> `complete_stage`); `handoff_project` to the Developer.
5. **Developer — implement & submit.** As **Software Developer**: read the construction brief,
   materialize it as a local Kiro spec, implement, run tests. Show the **tests-before-submit** hook
   blocking `submit_implementation` on a failing test, then passing. `link_repo` +
   `submit_implementation` -> ingestion advances to QA.
6. **QA — test & pass.** As **QA Engineer**: `run_tests`, generate the report
   (`get_stage_brief` -> `submit_artifact`), review results, `complete_stage` (QA passed), then
   `handoff_project` to DevOps. (Optionally bounce to the Developer to show QA->Dev.)
7. **DevOps — release.** As **DevOps Engineer**: review the plan, generate the deployment artifacts
   (`get_stage_brief` -> `submit_artifact`), then `complete_stage` through to `deployed`.

## Points to highlight

- One persona per identity; RBAC is enforced by the Project API (403/422) regardless of the Kiro
  client — the client agents/permissions/hooks are additive guardrails.
- The request-changes bounce always carries the reviewer's comments to the receiving persona and is
  never blocked by a gate.
- Same tools compose with other MCP servers (Jira/ServiceNow) if installed — see the spec's
  "Future third-party MCP integrations" note.
