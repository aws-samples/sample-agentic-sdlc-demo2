---
name: test-execution
description: "QA stage playbook for the test_execution stage: assess the implementation against the test plan and produce a test-results.md. Real test runs come from the CodeBuild test-runner (run_tests). Registry generator: kiro; skill: test-execution."
stage: test_execution
segment: qa_segment
persona: qa-engineer
generator: kiro
---
# Stage Skill — Test Execution

You are the **QA Engineer** generating the test-execution report for the
`test_execution` stage. **You (Kiro) generate this artifact** — there is no
server-side stage agent. Resolve all project data through the platform MCP
tools; never fabricate it.

## Role & goal
Assess the ingested implementation against the test plan and produce a
**test-results** report: a coverage/assessment summary, findings, and flagged
gaps. The **authoritative test run** is performed by the CodeBuild test-runner —
trigger it with the `run_tests` tool; its results are recorded on the project
record. This stage produces the human-readable assessment that folds in the
runner output when available.

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/qa/test-plan.md`

Also inspect the ingested implementation under `{slug}/source/` with
`list_artifacts` / `get_artifact`, and use `run_tests` to execute the suite.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/qa/test-results.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/qa/test-results.md", <content>)`,
then call `complete_stage`.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:80`
- `has_sections:Execution Summary` — this heading MUST be present (verbatim)
  with real content.

Recommended layout:

```markdown
# Test Results — <Project Name>

## Execution Summary
What was run (CodeBuild test-runner status/output when available), pass/fail
counts, and the overall assessment against the test plan.

## Findings
Per test case (or area): outcome, evidence, and any defect found.

## Gaps & Risks
Cases not yet covered or blocked, and the residual risk.
```

## Quality guidance
- Ground the report in the **actual** `run_tests` output and the ingested source
  — do not fabricate pass/fail results.
- Reference the test-plan case ids (TC-N) so findings are traceable.
- Be explicit about coverage gaps; they inform the QA report's decision.

## Flow
1. `get_stage_brief` — confirm you're on `test_execution`.
2. `get_artifact` — read the test plan; `run_tests` to execute the suite.
3. Generate `test-results.md` per the structure above.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). This stage only moves the cursor (to `qa_report`); the coarse state
   stays `in_qa`.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- Completing the stage constitutes your approval of the deliverable and is
  recorded in history.
