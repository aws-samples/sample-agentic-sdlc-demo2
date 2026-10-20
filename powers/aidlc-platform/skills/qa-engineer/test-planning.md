---
name: test-planning
description: "QA stage playbook for the test_planning stage: read the inception/design artifacts and produce a test-plan.md with a strategy and traceability from units of work to test cases. Registry generator: kiro; skill: test-planning; advances_state: in_qa."
stage: test_planning
segment: qa_segment
persona: qa-engineer
generator: kiro
---
# Stage Skill — Test Planning

You are the **QA Engineer** generating the test plan for the `test_planning`
stage. **You (Kiro) generate this artifact** — there is no server-side stage
agent. Resolve all project data through the platform MCP tools; never fabricate
it.

## Role & goal
Read the inception/design deliverables and produce a **test plan**: the test
strategy and a traceability outline from each unit of work (and story) to its
test cases. This is the first QA stage — completing it advances the coarse state
to `in_qa`. It performs no test execution — that is the `test_execution` stage
(and the CodeBuild test-runner).

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/inception/requirements/requirements.md`
- `aidlc-docs/inception/user-stories/stories.md`
- `aidlc-docs/inception/application-design/application-design.md`
- `aidlc-docs/inception/application-design/unit-of-work.md`

Read them with `get_artifact`. The ingested implementation source lives under
`{slug}/source/` once the Git round-trip has run — inspect it with
`list_artifacts` / `get_artifact` to ground the plan in the real code.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/qa/test-plan.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/qa/test-plan.md", <content>)`,
then call `complete_stage`.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:120`
- `has_sections:Test Strategy,Test Cases` — these headings MUST be present
  (verbatim), each with real content.

Recommended layout:

```markdown
# Test Plan — <Project Name>

## Test Strategy
Scope, test levels (unit/integration/e2e), environments, entry/exit criteria,
and the risk-based prioritization.

## Traceability
A table mapping each unit of work / story to the test cases that cover it.

## Test Cases
For each case: id, the unit/story it covers, preconditions, steps, and expected
result.
```

## Quality guidance
- Ensure **every unit of work and story is covered** by at least one case; list
  any coverage gaps explicitly.
- Make cases concrete and executable (clear steps + expected results).
- Number cases (TC-1, TC-2, …) so results can reference them.

## Flow
1. `get_stage_brief` — confirm you're on `test_planning`.
2. `get_artifact` / `list_artifacts` — read the design artifacts and the ingested
   source.
3. Generate `test-plan.md` per the structure above.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). Because this stage declares `advances_state: in_qa`, completing it
   advances the coarse owned state to `in_qa` and moves the cursor to
   `test_execution`.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- Completing the stage constitutes your approval of the deliverable and is
  recorded in history.
