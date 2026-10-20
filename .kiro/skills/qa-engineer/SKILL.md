---
name: qa-engineer
description: "QA Engineer workflow: run the test-runner on the ingested code, review results with AI assistance, then pass to DevOps or return to the developer. Use when acting as QA / qa-engineer."
---
# QA Engineer playbook

You test the ingested implementation.

## Steps

1. **Pick up your project.** `list_projects` (state `assigned_to_qa`/`in_qa`), then `get_project`.
2. **Run tests.** `run_tests` starts the CodeBuild test-runner; results arrive asynchronously —
   check `get_project` for `qa_test_status` / `qa_test_summary`.
3. **Generate the test plan/report.** For each QA stage, call `get_stage_brief`, generate the test
   plan / report in Kiro, upload it with `submit_artifact`, then `complete_stage` to validate and
   advance the stage cursor; review with `get_artifact`.
4. **Review (required before passing).** Read the test plan / results / `qa_report` with
   `get_artifact`. Perform an **AI-assisted review**: are the tests adequate for the code? any
   uncovered risks or flaky/failing areas?
5. **Decide.**
   - Acceptable: `complete_stage` (QA passed), then `handoff_project` to DevOps.
   - Not: `return_for_changes` to the Developer with the failing details.
